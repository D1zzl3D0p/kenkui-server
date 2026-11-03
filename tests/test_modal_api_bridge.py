"""Internal bridge contract, using real PostgreSQL and the pinned EPUB pipeline."""

import base64
import hashlib
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import kenkui as kk
import pytest
from fastapi.testclient import TestClient
from test_covers import PNG
from test_pipeline_reconstruction import write_epub

from kenkui_server.compute.modal import ModalProcessRunner
from kenkui_server.jobs.models import (
    Asset,
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.storage.assets import FakeS3Client, R2AssetStore
from kenkui_server.storage.postgres import PostgresHostedRepository, PostgresIdentityRepository
from kenkui_server.storage.postgres_database import PostgresDatabase

AUTH = {"X-Kenkui-Bridge-Secret": "synthetic-bridge-secret"}


def test_bridge_is_available():
    assert importlib.util.find_spec("kenkui_server.compute.api_bridge") is not None


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("idle", [False, True])
def test_deployment_exports_authenticated_bridge_without_network(monkeypatch, environment, idle):
    import runpy
    from pathlib import Path

    monkeypatch.setenv("KENKUI_DEPLOYMENT", environment)
    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1" if idle else "0")
    monkeypatch.delenv("KENKUI_MODAL_PROXY", raising=False)
    import modal

    real_asgi_app = modal.asgi_app
    configurations = []

    def asgi_app(**options):
        configurations.append(options)
        return real_asgi_app(**options)

    monkeypatch.setattr(modal, "asgi_app", asgi_app)
    deployed = runpy.run_path(str(Path(__file__).parents[1] / "deploy/modal_app.py"))
    assert isinstance(deployed["api_bridge"], modal.Function)
    assert configurations == [{"requires_proxy_auth": True}]


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    from kenkui_server.compute.api_bridge import create_bridge_app

    url = os.environ.get("KENKUI_TEST_DATABASE_URL")
    if not url:
        pytest.skip("disposable PostgreSQL required")
    schema = "bridge_test_" + uuid4().hex
    admin = PostgresDatabase(url)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    database = PostgresDatabase(url, schema=schema)
    database.migrate()
    repo = PostgresHostedRepository(database)
    owner = PostgresIdentityRepository(database).user_id_for_subject("synthetic-owner")
    source = str(uuid4())

    class CheckedObjects(R2AssetStore):
        def put_source(self, identifier, payload):
            assert getattr(database._state, "connection", None) is None
            return super().put_source(identifier, payload)

        @contextmanager
        def materialize_source(self, identifier):
            assert getattr(database._state, "connection", None) is None
            with super().materialize_source(identifier) as path:
                yield path

    objects = CheckedObjects(FakeS3Client(), bucket="synthetic", key_salt="synthetic")
    data = write_epub(tmp_path / "source.epub").read_bytes()
    objects.put_source(source, data)
    repo.assets.put_for_owner(
        Asset(source, source, hashlib.sha256(data).hexdigest(), "epub"), owner
    )
    submissions = []

    class ModalFunction:
        def spawn(self, dispatch_id, token):
            # The lease transaction must already be committed before network I/O.
            assert getattr(database._state, "connection", None) is None
            submissions.append((dispatch_id, token))

    monkeypatch.setattr("modal.Function.from_name", lambda *a, **kw: ModalFunction())
    app = create_bridge_app(
        environment="staging",
        secret="synthetic-bridge-secret",
        services_factory=lambda: SimpleNamespace(
            repositories=repo,
            assets=objects,
            voices=(kk.Voice("narrator", "Narrator", True, "local", "test", True),),
            runner=ModalProcessRunner(repo, app_name="kenkui-staging", max_jobs=1),
        ),
    )
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                repo=repo,
                database=database,
                objects=objects,
                owner=owner,
                source=source,
                submissions=submissions,
                body={"environment": "staging", "ownerId": str(owner), "sourceId": source},
            )
    finally:
        database.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_auth_rejected_before_services_or_json_parsing():
    from kenkui_server.compute.api_bridge import create_bridge_app

    def forbidden():
        pytest.fail("unauthenticated request opened processing dependencies")

    with TestClient(
        create_bridge_app(
            environment="staging", secret="synthetic-bridge-secret", services_factory=forbidden
        )
    ) as client:
        for headers in ({}, {"X-Kenkui-Bridge-Secret": "wrong"}):
            for operation in ("inspect", "preflight", "replace_cover", "dispatch"):
                assert (
                    client.post(f"/{operation}", content=b"not json", headers=headers).status_code
                    == 401
                )


def test_authenticated_requests_are_size_bounded_before_services():
    from kenkui_server.compute.api_bridge import MAX_REQUEST_BYTES, create_bridge_app

    def forbidden():
        pytest.fail("oversized request opened provider dependencies")

    with TestClient(
        create_bridge_app(
            environment="staging", secret="synthetic-bridge-secret", services_factory=forbidden
        )
    ) as client:
        response = client.post(
            "/inspect", content=iter([b"x" * MAX_REQUEST_BYTES, b"x"]), headers=AUTH
        )
        assert response.status_code == 413


def test_document_deadline_returns_504_without_waiting_for_processing():
    import threading

    from kenkui_server.compute.api_bridge import create_bridge_app

    entered = threading.Event()
    release = threading.Event()

    class Assets:
        def get(self, source):
            entered.set()
            release.wait(2)
            raise KeyError(source)

    with TestClient(
        create_bridge_app(
            environment="staging",
            secret="synthetic-bridge-secret",
            services_factory=lambda: SimpleNamespace(repositories=SimpleNamespace(assets=Assets())),
            request_timeout_seconds=0.01,
        )
    ) as client:
        try:
            response = client.post(
                "/inspect",
                json={
                    "environment": "staging",
                    "ownerId": str(uuid4()),
                    "sourceId": str(uuid4()),
                },
                headers=AUTH,
            )
            assert response.status_code == 504
            assert entered.is_set()
            assert not release.is_set()
        finally:
            release.set()


@pytest.fixture
def blocked_source():
    import threading

    lock = threading.Lock()
    release = threading.Event()
    entered = threading.Event()
    completed = threading.Event()
    closed = threading.Event()
    state = SimpleNamespace(active=0, peak=0, active_when_closed=None)

    class Assets:
        def get(self, source):
            with lock:
                state.active += 1
                state.peak = max(state.peak, state.active)
                entered.set()
            try:
                release.wait(5)
                raise KeyError(source)
            finally:
                with lock:
                    state.active -= 1
                    completed.set()

    def close():
        state.active_when_closed = state.active
        closed.set()

    state.services = SimpleNamespace(
        repositories=SimpleNamespace(assets=Assets()),
        close=close,
    )
    state.release = release
    state.entered = entered
    state.completed = completed
    state.closed = closed
    state.body = {
        "environment": "staging",
        "ownerId": str(uuid4()),
        "sourceId": str(uuid4()),
    }
    try:
        yield state
    finally:
        release.set()


def test_timed_out_operations_keep_capacity_and_reject_retries_until_completed(blocked_source):
    from kenkui_server.compute.api_bridge import create_bridge_app

    state = blocked_source
    app = create_bridge_app(
        environment="staging",
        secret="synthetic-bridge-secret",
        services_factory=lambda: state.services,
        request_timeout_seconds=0.01,
        max_operations=1,
    )
    with TestClient(app) as client:
        import anyio

        async def limit_threads():
            anyio.to_thread.current_default_thread_limiter().total_tokens = 1

        client.portal.call(limit_threads)
        try:
            responses = [client.post("/inspect", json=state.body, headers=AUTH) for _ in range(3)]
            assert [response.status_code for response in responses] == [504, 429, 429]
            assert state.active == 1
            assert state.peak == 1
        finally:
            state.release.set()
        assert state.completed.wait(2)
        # A completed operation returns its slot, so subsequent requests resume.
        response = client.post("/inspect", json=state.body, headers=AUTH)
        assert response.status_code == 404
    assert state.active_when_closed == 0


def test_shutdown_drains_timed_out_operations_before_closing_dependencies(blocked_source):
    import threading

    from kenkui_server.compute.api_bridge import create_bridge_app

    state = blocked_source
    client = TestClient(
        create_bridge_app(
            environment="staging",
            secret="synthetic-bridge-secret",
            services_factory=lambda: state.services,
            request_timeout_seconds=0.01,
            max_operations=1,
        )
    )
    client.__enter__()
    assert client.post("/inspect", json=state.body, headers=AUTH).status_code == 504
    shutdown = threading.Thread(target=lambda: client.__exit__(None, None, None))
    shutdown.start()
    try:
        assert not state.closed.wait(0.05)
        assert state.active == 1
    finally:
        state.release.set()
        shutdown.join(2)
    assert not shutdown.is_alive()
    assert state.closed.is_set()
    assert state.active_when_closed == 0


def test_cancelling_client_keeps_operation_tracked_and_capacity_recovers(blocked_source):
    import anyio
    import httpx

    from kenkui_server.compute.api_bridge import create_bridge_app

    state = blocked_source
    app = create_bridge_app(
        environment="staging",
        secret="synthetic-bridge-secret",
        services_factory=lambda: state.services,
        request_timeout_seconds=1,
        max_operations=1,
    )

    async def scenario():
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://bridge.test",
            ) as client:
                async with anyio.create_task_group() as requests:
                    requests.start_soon(
                        lambda: client.post("/inspect", json=state.body, headers=AUTH)
                    )
                    with anyio.fail_after(2):
                        while not state.entered.is_set():
                            await anyio.sleep(0.001)
                    requests.cancel_scope.cancel()
                try:
                    response = await client.post("/inspect", json=state.body, headers=AUTH)
                    assert response.status_code == 429
                    assert state.active == 1
                    assert state.peak == 1
                    assert not state.closed.is_set()
                finally:
                    state.release.set()
                with anyio.fail_after(2):
                    while not state.completed.is_set():
                        await anyio.sleep(0.001)
                assert (
                    await client.post("/inspect", json=state.body, headers=AUTH)
                ).status_code == 404
        assert state.active_when_closed == 0

    anyio.run(scenario)


def test_environment_owner_and_strict_ids_cannot_select_foreign_objects(bridge):
    for patch, expected in [
        ({"environment": "production"}, 403),
        ({"ownerId": str(uuid4())}, 403),
        ({"sourceId": str(uuid4())}, 404),
        ({"sourceId": "../source"}, 422),
        ({"url": "https://example.invalid/secret"}, 422),
        ({"path": "/tmp/book.epub"}, 422),
        ({"function": "render_job"}, 422),
    ]:
        assert (
            bridge.client.post("/inspect", json=bridge.body | patch, headers=AUTH).status_code
            == expected
        )


def test_inspect_and_preflight_use_the_existing_epub_pipeline(bridge):
    response = bridge.client.post("/inspect", json=bridge.body, headers=AUTH)
    assert response.status_code == 200, response.text
    book = response.json()
    assert book["title"] == "Tiny"
    assert book["author"] == "Ada"
    assert len(book["chapters"]) == 2
    intent = {
        "sourceId": bridge.source,
        "chapters": [c["id"] for c in book["chapters"]],
        "casting": {"voiceId": "narrator"},
    }
    result = bridge.client.post(
        "/preflight",
        json={
            "environment": "staging",
            "ownerId": str(bridge.owner),
            "job": intent,
        },
        headers=AUTH,
    )
    assert result.status_code == 200, result.text
    assert result.json()["normalizedCharacters"] == 6
    assert result.json()["sourceId"] == bridge.source
    assert bridge.database.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0
    for patch in (
        {"function": "exec"},
        {"tts": {"prepareNumbers": "true"}},
        {"casting": {"voiceId": "narrator", "path": "/tmp/x"}},
    ):
        invalid = bridge.client.post(
            "/preflight",
            json={
                "environment": "staging",
                "ownerId": str(bridge.owner),
                "job": intent | patch,
            },
            headers=AUTH,
        )
        assert invalid.status_code == 422


def test_pdf_remains_unsupported_without_materializing_it(bridge, monkeypatch):
    bridge.database.execute("UPDATE assets SET format='pdf' WHERE id=%s", (bridge.source,))
    monkeypatch.setattr(bridge.objects, "materialize_source", lambda _: pytest.fail("PDF parsed"))
    assert bridge.client.post("/inspect", json=bridge.body, headers=AUTH).status_code == 415


def test_cover_is_immutable_and_concurrent_retries_return_identical_asset(bridge, monkeypatch):
    body = bridge.body | {"coverBase64": base64.b64encode(PNG).decode(), "mediaType": "image/png"}
    from threading import Barrier

    before = bridge.objects.read_source(bridge.source)
    barrier = Barrier(2)
    real_put = bridge.objects.put_source

    def simultaneous_put(identifier, payload):
        barrier.wait(2)  # Both contenders must pass the no-existing-row check.
        return real_put(identifier, payload)

    monkeypatch.setattr(bridge.objects, "put_source", simultaneous_put)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: bridge.client.post("/replace_cover", json=body, headers=AUTH), range(2)
            )
        )
    monkeypatch.setattr(bridge.objects, "put_source", real_put)
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    first = responses[0].json()
    assert first == responses[1].json()
    assert first["id"] != bridge.source
    assert bridge.objects.read_source(bridge.source) == before
    stored = bridge.objects.read_source(first["id"])
    assert hashlib.sha256(stored).hexdigest() == first["sha256"]
    assert bridge.client.post("/replace_cover", json=body, headers=AUTH).json() == first
    assert bridge.repo.assets.owner_id(first["id"]) == bridge.owner
    assert bridge.database.execute("SELECT COUNT(*) AS n FROM assets").fetchone()["n"] == 2


def test_deterministic_cover_bytes_do_not_depend_on_wall_clock(bridge, monkeypatch):
    from kenkui_server.api.covers import replace_cover

    source = bridge.objects.read_source(bridge.source)
    identifier = uuid4()
    monkeypatch.setattr("zipfile.time.localtime", lambda *args: (2020, 1, 1, 0, 0, 0, 2, 1, 0))
    first = replace_cover(source, PNG, identifier=identifier)
    monkeypatch.setattr("zipfile.time.localtime", lambda *args: (2022, 3, 2, 4, 6, 8, 2, 1, 0))
    second = replace_cover(source, PNG, identifier=identifier)
    assert first == second


def test_cover_upload_failure_can_retry_without_metadata_or_source_mutation(bridge, monkeypatch):
    body = bridge.body | {"coverBase64": base64.b64encode(PNG).decode(), "mediaType": "image/png"}
    real_put = bridge.objects.put_source

    def fail_after_upload(identifier, data):
        real_put(identifier, data)
        raise RuntimeError("synthetic ambiguous upload")

    monkeypatch.setattr(bridge.objects, "put_source", fail_after_upload)
    with pytest.raises(RuntimeError, match="synthetic ambiguous"):
        bridge.client.post("/replace_cover", json=body, headers=AUTH)
    assert bridge.database.execute("SELECT COUNT(*) AS n FROM assets").fetchone()["n"] == 1
    monkeypatch.setattr(bridge.objects, "put_source", real_put)
    result = bridge.client.post("/replace_cover", json=body, headers=AUTH)
    assert result.status_code == 200
    assert (
        hashlib.sha256(bridge.objects.read_source(result.json()["id"])).hexdigest()
        == result.json()["sha256"]
    )


def admit(bridge):
    account = str(uuid4())
    row = bridge.database.execute(
        "INSERT INTO credit_accounts (id,identity_id) VALUES (%s,%s) "
        "ON CONFLICT(identity_id) DO UPDATE SET identity_id=EXCLUDED.identity_id RETURNING id",
        (account, str(bridge.owner)),
    ).fetchone()
    account = str(row["id"])
    bridge.repo.billing.grant(account, 10, reference="synthetic-grant:" + str(uuid4()))
    job = Job(
        str(uuid4()),
        JobSpec(
            bridge.source,
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("out.m4b"),
        ),
    )
    dispatch = Dispatch(str(uuid4()), job.id, "pending")
    bridge.repo.admit(
        job,
        dispatch,
        account_id=account,
        owner_id=str(bridge.owner),
        credits=1,
        idempotency_key=None,
    )
    return dispatch


def test_dispatch_repeats_and_concurrent_deliveries_obey_existing_leases(bridge):
    dispatch = admit(bridge)
    body = {"environment": "staging", "dispatchId": dispatch.id}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: bridge.client.post("/dispatch", json=body, headers=AUTH), range(2))
        )
    assert [r.json() for r in responses] == [{"accepted": True}, {"accepted": True}]
    assert len(bridge.submissions) == 1
    assert (
        bridge.client.post(
            "/dispatch", json=body | {"ownerId": str(bridge.owner)}, headers=AUTH
        ).status_code
        == 422
    )
    second = admit(bridge)
    assert bridge.client.post(
        "/dispatch", json={"environment": "staging", "dispatchId": second.id}, headers=AUTH
    ).json() == {"accepted": True}
    assert len(bridge.submissions) == 1


def test_ambiguous_spawn_keeps_lease_and_credit_reservation(bridge, monkeypatch):
    class Ambiguous:
        def spawn(self, *args):
            raise TimeoutError("synthetic transport timeout")

    monkeypatch.setattr("modal.Function.from_name", lambda *a, **kw: Ambiguous())
    dispatch = admit(bridge)
    body = {"environment": "staging", "dispatchId": dispatch.id}
    assert bridge.client.post("/dispatch", json=body, headers=AUTH).json() == {"accepted": True}
    assert bridge.repo.claim_execution(dispatch.id, limit=2, ttl=900) is None
    assert bridge.repo.billing.authorization_for_job(dispatch.job_id).status.value == "reserved"
