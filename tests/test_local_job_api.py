from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import kenkui as kk
from fastapi.testclient import TestClient

from kenkui_server.app import create_app


def _epub() -> bytes:
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles>"
            '<rootfile full-path="OPS/book.opf"/>'
            "</rootfiles>"
            "</container>",
        )
        archive.writestr(
            "OPS/book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Tiny</dc:title>"
            "<dc:creator>Ada</dc:creator>"
            "</metadata>"
            "<manifest>"
            '<item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>'
            "</manifest>"
            "<spine>"
            '<itemref idref="one"/>'
            "</spine>"
            "</package>",
        )
        archive.writestr(
            "OPS/one.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello narrator.</p></body></html>',
        )
    return result.getvalue()


def test_preflight_then_idempotent_job_creation(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(
        create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)
    ) as client:
        asset = client.post(
            "/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()
        book = client.get(f"/v1/assets/{asset['id']}/book").json()
        request = {
            "sourceId": asset["id"],
            "chapters": [book["chapters"][0]["id"]],
            "casting": {"voiceId": "narrator"},
            "output": {"format": "m4b"},
        }

        preflight = client.post("/v1/jobs/preflight", json=request)
        first = client.post("/v1/jobs", json=request, headers={"Idempotency-Key": "retry-1"})
        replay = client.post("/v1/jobs", json=request, headers={"Idempotency-Key": "retry-1"})

        assert preflight.json()["normalizedCharacters"] == 15
        assert first.status_code == 202
        assert replay.json()["id"] == first.json()["id"]
        assert "path" not in first.text


def test_list_jobs_returns_authoritative_snapshots(tmp_path: Path) -> None:
    from kenkui_server.jobs.models import (
        Job,
        JobSpec,
        JobStatus,
        OutputSpec,
        Progress,
        SingleVoiceCasting,
        TtsSettings,
    )

    app = create_app(data_dir=tmp_path / "state", fixture_mode=True)
    repositories = app.state.local_services.repositories
    repositories.jobs.create(
        Job(
            "job-b",
            JobSpec(
                "source-b",
                ("chapter-1",),
                SingleVoiceCasting("narrator"),
                TtsSettings(),
                OutputSpec("artifact.m4b"),
            ),
        )
    )
    repositories.jobs.create(
        Job(
            "job-a",
            JobSpec(
                "source-a",
                ("chapter-1",),
                SingleVoiceCasting("narrator"),
                TtsSettings(),
                OutputSpec("artifact.m4b"),
            ),
            status=JobStatus.RUNNING,
            version=1,
            progress=Progress("synthesis", 2, 3),
        )
    )

    with TestClient(app) as client:
        response = client.get("/v1/jobs")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "sourceId": "source-a",
                "sourceCover": True,
                "castingMode": "single",
                "narratorVoiceId": "narrator",
                "id": "job-a",
                "status": "running",
                "progress": {"stage": "synthesis", "completed": 2, "total": 3},
            },
            {
                "sourceId": "source-b",
                "sourceCover": True,
                "castingMode": "single",
                "narratorVoiceId": "narrator",
                "id": "job-b",
                "status": "queued",
                "progress": {"stage": "queued", "completed": 0, "total": 0},
            },
        ]
    }


def test_cancelling_running_job_returns_a_durable_cancellation_request(tmp_path: Path) -> None:
    from kenkui_server.jobs.models import (
        Job,
        JobSpec,
        JobStatus,
        OutputSpec,
        Progress,
        SingleVoiceCasting,
        TtsSettings,
    )

    app = create_app(data_dir=tmp_path / "state", fixture_mode=True)
    repositories = app.state.local_services.repositories
    job = Job(
        "job-running",
        JobSpec(
            "source",
            ("chapter-1",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        ),
        status=JobStatus.RUNNING,
        version=1,
        progress=Progress("synthesis", 1, 3),
    )
    repositories.jobs.create(job)

    with TestClient(app) as client:
        first = client.post(f"/v1/jobs/{job.id}/cancel")
        second = client.post(f"/v1/jobs/{job.id}/cancel")

    expected = {
        "id": job.id,
        "status": "cancel_requested",
        "progress": {"stage": "synthesis", "completed": 1, "total": 3},
    }
    assert first.status_code == 200
    expected.update(
        sourceId="source", sourceCover=True, castingMode="single", narratorVoiceId="narrator"
    )
    assert first.json() == expected
    assert second.json() == expected
    assert repositories.jobs.get(job.id).status is JobStatus.CANCEL_REQUESTED
    assert [event.event_type for event in repositories.events.list_for_job(job.id)] == [
        "cancel_requested"
    ]


def test_cancellation_after_dispatch_finalization_terminalizes_atomically(tmp_path: Path) -> None:
    from kenkui_server.jobs.models import (
        Dispatch,
        Job,
        JobSpec,
        JobStatus,
        OutputSpec,
        Progress,
        SingleVoiceCasting,
        TtsSettings,
    )

    app = create_app(data_dir=tmp_path / "state", fixture_mode=True)
    repositories = app.state.local_services.repositories
    job = Job(
        "job-finalized-dispatch",
        JobSpec(
            "source",
            ("chapter-1",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        ),
        status=JobStatus.RUNNING,
        version=1,
        progress=Progress("synthesis", 1, 3),
    )
    repositories.create_job_and_dispatch(
        job,
        Dispatch("dispatch-finalized", job.id, "pending"),
        idempotency_key=None,
    )
    repositories.dispatches.update(
        Dispatch("dispatch-finalized", job.id, "done", 1), expected_version=0
    )

    with TestClient(app) as client:
        first = client.post(f"/v1/jobs/{job.id}/cancel")
        second = client.post(f"/v1/jobs/{job.id}/cancel")

    expected = {
        "id": job.id,
        "status": "cancelled",
        "progress": {"stage": "synthesis", "completed": 1, "total": 3},
    }
    assert first.status_code == 200
    expected.update(
        sourceId="source", sourceCover=True, castingMode="single", narratorVoiceId="narrator"
    )
    assert first.json() == expected
    assert second.json() == expected
    assert repositories.jobs.get(job.id).status is JobStatus.CANCELLED
    assert repositories.dispatches.get("dispatch-finalized").status == "done"
    assert [event.event_type for event in repositories.events.list_for_job(job.id)] == ["cancelled"]


def test_fixture_worker_publishes_one_authorized_artifact(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(
        create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)
    ) as client:
        asset = client.post(
            "/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()
        book = client.get(f"/v1/assets/{asset['id']}/book").json()
        job = client.post(
            "/v1/jobs",
            json={
                "sourceId": asset["id"],
                "chapters": [book["chapters"][0]["id"]],
                "casting": {"voiceId": "narrator"},
                "output": {"format": "m4b"},
            },
        ).json()

        for _ in range(50):
            snapshot = client.get(f"/v1/jobs/{job['id']}").json()
            if snapshot["status"] not in {"queued", "running"}:
                break
            __import__("time").sleep(0.02)

        assert snapshot["status"] == "succeeded"
        artifact = client.get(f"/v1/jobs/{job['id']}/artifact")
        assert artifact.status_code == 200
        assert artifact.content == b"KENKUI-FIXTURE-M4B\n"


def test_cancellation_is_idempotent_and_artifact_remains_private(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(
        create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)
    ) as client:
        asset = client.post(
            "/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()
        book = client.get(f"/v1/assets/{asset['id']}/book").json()
        job = client.post(
            "/v1/jobs",
            json={
                "sourceId": asset["id"],
                "chapters": [book["chapters"][0]["id"]],
                "casting": {"voiceId": "narrator"},
                "output": {"format": "m4b"},
            },
        ).json()

        first = client.post(f"/v1/jobs/{job['id']}/cancel")
        second = client.post(f"/v1/jobs/{job['id']}/cancel")

        assert first.status_code == 200
        assert second.json() == first.json()
        assert first.json()["status"] == "cancelled"
        assert client.get(f"/v1/jobs/{job['id']}/artifact").status_code == 409


def test_sse_replays_monotonic_durable_history(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(
        create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)
    ) as client:
        asset = client.post(
            "/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()
        book = client.get(f"/v1/assets/{asset['id']}/book").json()
        job = client.post(
            "/v1/jobs",
            json={
                "sourceId": asset["id"],
                "chapters": [book["chapters"][0]["id"]],
                "casting": {"voiceId": "narrator"},
                "output": {"format": "m4b"},
            },
        ).json()
        for _ in range(50):
            snapshot = client.get(f"/v1/jobs/{job['id']}").json()
            if snapshot["status"] == "succeeded":
                break
            __import__("time").sleep(0.02)

        stream = client.get(f"/v1/jobs/{job['id']}/events")
        identifiers = [line for line in stream.text.splitlines() if line.startswith("id: ")]

        assert stream.headers["content-type"].startswith("text/event-stream")
        assert identifiers == sorted(identifiers, key=lambda item: int(item[4:]))
        assert len(identifiers) >= 2


def test_restart_recovers_unclaimed_dispatch_from_durable_state(tmp_path: Path) -> None:
    from kenkui_server.jobs.models import (
        Asset,
        Dispatch,
        Job,
        JobSpec,
        OutputSpec,
        SingleVoiceCasting,
        TtsSettings,
    )

    root = tmp_path / "state"
    first = create_app(data_dir=root, fixture_mode=True)
    services = first.state.local_services
    source = services.assets.put_source("asset-restart", _epub())
    services.repositories.assets.put(Asset("asset-restart", str(source), "digest", "epub"))
    job = Job(
        "job-restart",
        JobSpec(
            "asset-restart",
            ("chapter-1",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        ),
    )
    services.repositories.create_job_and_dispatch(
        job, Dispatch("dispatch-restart", job.id, "pending"), idempotency_key=None
    )

    restarted = create_app(data_dir=root, fixture_mode=True)
    for _ in range(50):
        snapshot = restarted.state.local_services.repositories.jobs.get(job.id)
        if snapshot.status.value == "succeeded":
            break
        __import__("time").sleep(0.02)

    assert snapshot.status.value == "succeeded"


def test_running_worker_polls_durable_cancellation(monkeypatch, tmp_path: Path) -> None:
    import threading
    import time

    from kenkui_server.jobs.models import (
        Asset,
        Dispatch,
        Job,
        JobSpec,
        OutputSpec,
        SingleVoiceCasting,
        TtsSettings,
    )
    from kenkui_server.jobs.transitions import CancelRequested, transition
    from kenkui_server.storage.assets import AssetStore
    from kenkui_server.storage.database import Database
    from kenkui_server.storage.repositories import Repositories
    from kenkui_server.worker import LocalJobRunner

    root = tmp_path / "state"
    store = AssetStore(root / "assets")
    database = Database(root / "server.sqlite3")
    repositories = Repositories(database)
    source = store.put_source("asset-cancel", b"source")
    repositories.assets.put(Asset("asset-cancel", str(source), "digest", "epub"))
    job = Job(
        "job-cancel",
        JobSpec(
            "asset-cancel",
            ("chapter-1",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        ),
    )
    repositories.create_job_and_dispatch(
        job, Dispatch("dispatch-cancel", job.id, "pending"), idempotency_key=None
    )
    observed = threading.Event()

    class BlockingPipeline:
        def write(self, output, *, cancel, **_):
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if cancel.cancelled:
                    observed.set()
                    cancel.raise_if_cancelled()
                time.sleep(0.01)
            raise AssertionError("worker did not poll cancellation")

    monkeypatch.setattr("kenkui_server.worker.pipeline_from_job", lambda *_: BlockingPipeline())
    worker = LocalJobRunner(database.path, store.root)
    thread = threading.Thread(target=worker.run, args=("dispatch-cancel",))
    thread.start()
    for _ in range(50):
        current = repositories.jobs.get(job.id)
        if current.status.value == "running":
            break
        time.sleep(0.01)
    repositories.jobs.update(
        transition(current, CancelRequested()), expected_version=current.version
    )
    thread.join(timeout=1)

    assert observed.is_set()
    assert repositories.jobs.get(job.id).status.value == "cancelled"


def test_stale_completion_after_cancellation_discards_output_and_terminalizes_job(
    monkeypatch, tmp_path: Path
) -> None:
    from kenkui_server.jobs.models import (
        Asset,
        Dispatch,
        Job,
        JobSpec,
        JobStatus,
        OutputSpec,
        Progress,
        SingleVoiceCasting,
        TtsSettings,
    )
    from kenkui_server.jobs.transitions import CancelRequested, transition
    from kenkui_server.storage.assets import AssetStore
    from kenkui_server.storage.database import Database
    from kenkui_server.storage.repositories import Repositories
    from kenkui_server.worker import LocalJobRunner

    root = tmp_path / "state"
    store = AssetStore(root / "assets")
    database = Database(root / "server.sqlite3")
    repositories = Repositories(database)
    source = store.put_source("asset-stale-completion", b"source")
    repositories.assets.put(Asset("asset-stale-completion", str(source), "digest", "epub"))
    job = Job(
        "job-stale-completion",
        JobSpec(
            "asset-stale-completion",
            ("chapter-1",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        ),
        status=JobStatus.RUNNING,
        version=1,
        progress=Progress("synthesis", 0, 1),
    )
    repositories.create_job_and_dispatch(
        job, Dispatch("dispatch-stale-completion", job.id, "pending"), idempotency_key=None
    )
    get_job = repositories.jobs.get
    get_calls = 0

    def get_with_interleaved_cancellation(job_id: str) -> Job:
        nonlocal get_calls
        snapshot = get_job(job_id)
        get_calls += 1
        if get_calls == 2:
            cancellation = transition(snapshot, CancelRequested())
            repositories.update_job_and_append_event(
                cancellation, expected_version=snapshot.version, event_type="cancel_requested"
            )
        return snapshot

    update_dispatch = repositories.dispatches.update

    def update_dispatch_after_terminal_cancellation(
        dispatch: Dispatch, *, expected_version: int
    ) -> None:
        if dispatch.status == "done":
            assert get_job(job.id).status is JobStatus.CANCELLED
            assert [event.event_type for event in repositories.events.list_for_job(job.id)] == [
                "cancel_requested",
                "cancelled",
            ]
        update_dispatch(dispatch, expected_version=expected_version)

    monkeypatch.setattr(repositories.jobs, "get", get_with_interleaved_cancellation)
    monkeypatch.setattr(
        repositories.dispatches, "update", update_dispatch_after_terminal_cancellation
    )

    try:
        LocalJobRunner(database.path, store.root, fixture_mode=True)._run(
            repositories, store, "dispatch-stale-completion"
        )

        assert not store.artifact_path(job.id).exists()
        assert repositories.artifacts.list_for_job(job.id) == ()
        assert repositories.dispatches.get("dispatch-stale-completion").status == "done"
    finally:
        database.close()


def test_cancellation_committed_after_stale_completion_recovery_stays_recoverable(
    monkeypatch, tmp_path: Path
) -> None:
    from kenkui_server.jobs.models import (
        Asset,
        Dispatch,
        Job,
        JobSpec,
        JobStatus,
        OutputSpec,
        Progress,
        SingleVoiceCasting,
        TtsSettings,
    )
    from kenkui_server.jobs.transitions import CancelRequested, transition
    from kenkui_server.storage.assets import AssetStore
    from kenkui_server.storage.database import Database
    from kenkui_server.storage.repositories import Repositories, StaleWriteError
    from kenkui_server.worker import LocalJobRunner

    root = tmp_path / "state"
    store = AssetStore(root / "assets")
    database = Database(root / "server.sqlite3")
    repositories = Repositories(database)
    source = store.put_source("asset-post-reload-cancellation", b"source")
    repositories.assets.put(Asset("asset-post-reload-cancellation", str(source), "digest", "epub"))
    job = Job(
        "job-post-reload-cancellation",
        JobSpec(
            "asset-post-reload-cancellation",
            ("chapter-1",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        ),
        status=JobStatus.RUNNING,
        version=1,
        progress=Progress("synthesis", 0, 1),
    )
    repositories.create_job_and_dispatch(
        job, Dispatch("dispatch-post-reload-cancellation", job.id, "pending"), idempotency_key=None
    )
    update_job = repositories.update_job_and_append_event

    def reject_completed_snapshot(*args, event_type: str, **kwargs) -> None:
        if event_type == "completed":
            raise StaleWriteError()
        update_job(*args, event_type=event_type, **kwargs)

    get_job = repositories.jobs.get
    get_calls = 0

    def get_with_post_recovery_cancellation(job_id: str) -> Job:
        nonlocal get_calls
        snapshot = get_job(job_id)
        get_calls += 1
        if get_calls == 3:
            cancellation = transition(snapshot, CancelRequested())
            update_job(
                cancellation, expected_version=snapshot.version, event_type="cancel_requested"
            )
        return snapshot

    monkeypatch.setattr(repositories, "update_job_and_append_event", reject_completed_snapshot)
    monkeypatch.setattr(repositories.jobs, "get", get_with_post_recovery_cancellation)

    try:
        LocalJobRunner(database.path, store.root, fixture_mode=True)._run(
            repositories, store, "dispatch-post-reload-cancellation"
        )
        assert repositories.jobs.get(job.id).status is JobStatus.CANCELLED
        assert repositories.dispatches.get("dispatch-post-reload-cancellation").status == "done"
        assert not store.artifact_path(job.id).exists()
        assert repositories.artifacts.list_for_job(job.id) == ()

        assert [event.event_type for event in repositories.events.list_for_job(job.id)] == [
            "cancel_requested",
            "cancelled",
        ]
    finally:
        database.close()


def test_sse_honors_last_event_id_for_incremental_reconnect(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(
        create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)
    ) as client:
        asset = client.post(
            "/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()
        book = client.get(f"/v1/assets/{asset['id']}/book").json()
        job = client.post(
            "/v1/jobs",
            json={
                "sourceId": asset["id"],
                "chapters": [book["chapters"][0]["id"]],
                "casting": {"voiceId": "narrator"},
            },
        ).json()
        for _ in range(50):
            if client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded":
                break
            __import__("time").sleep(0.02)

        stream = client.get(f"/v1/jobs/{job['id']}/events", headers={"Last-Event-ID": "1"})

        assert "id: 1\n" not in stream.text
        assert "id: 2\n" in stream.text


def test_restart_reclaims_running_dispatch_after_worker_death(tmp_path: Path) -> None:
    from kenkui_server.jobs.models import (
        Asset,
        Dispatch,
        Job,
        JobSpec,
        JobStatus,
        OutputSpec,
        Progress,
        SingleVoiceCasting,
        TtsSettings,
    )

    root = tmp_path / "state"
    first = create_app(data_dir=root, fixture_mode=True)
    services = first.state.local_services
    source = services.assets.put_source("asset-running", _epub())
    services.repositories.assets.put(Asset("asset-running", str(source), "digest", "epub"))
    job = Job(
        "job-running",
        JobSpec(
            "asset-running",
            ("chapter-1",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        ),
        status=JobStatus.RUNNING,
        version=1,
        progress=Progress("running", 0, 0),
    )
    services.repositories.jobs.create(job)
    services.repositories.dispatches.create(
        Dispatch("dispatch-running", job.id, "running", version=1)
    )

    restarted = create_app(data_dir=root, fixture_mode=True)
    for _ in range(50):
        snapshot = restarted.state.local_services.repositories.jobs.get(job.id)
        if snapshot.status.value == "succeeded":
            break
        __import__("time").sleep(0.02)

    assert snapshot.status.value == "succeeded"
