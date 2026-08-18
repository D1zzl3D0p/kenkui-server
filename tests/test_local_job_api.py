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
        archive.writestr("META-INF/container.xml", """<container xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\"><rootfiles><rootfile full-path=\"OPS/book.opf\"/></rootfiles></container>""")
        archive.writestr("OPS/book.opf", """<package xmlns=\"http://www.idpf.org/2007/opf\" version=\"3.0\"><metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\"><dc:title>Tiny</dc:title><dc:creator>Ada</dc:creator></metadata><manifest><item id=\"one\" href=\"one.xhtml\" media-type=\"application/xhtml+xml\"/></manifest><spine><itemref idref=\"one\"/></spine></package>""")
        archive.writestr("OPS/one.xhtml", "<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><p>Hello narrator.</p></body></html>")
    return result.getvalue()


def test_preflight_then_idempotent_job_creation(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)) as client:
        asset = client.post("/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}).json()
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


def test_fixture_worker_publishes_one_authorized_artifact(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)) as client:
        asset = client.post("/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}).json()
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
    with TestClient(create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)) as client:
        asset = client.post("/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}).json()
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
    with TestClient(create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)) as client:
        asset = client.post("/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}).json()
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
    from kenkui_server.jobs.models import Asset, Dispatch, Job, JobSpec, OutputSpec, SingleVoiceCasting, TtsSettings

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
    services.repositories.create_job_and_dispatch(job, Dispatch("dispatch-restart", job.id, "pending"), idempotency_key=None)

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

    from kenkui_server.jobs.models import Asset, Dispatch, Job, JobSpec, OutputSpec, SingleVoiceCasting, TtsSettings
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
    job = Job("job-cancel", JobSpec("asset-cancel", ("chapter-1",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("artifact.m4b")))
    repositories.create_job_and_dispatch(job, Dispatch("dispatch-cancel", job.id, "pending"), idempotency_key=None)
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
    repositories.jobs.update(transition(current, CancelRequested()), expected_version=current.version)
    thread.join(timeout=1)

    assert observed.is_set()
    assert repositories.jobs.get(job.id).status.value == "cancelled"


def test_sse_honors_last_event_id_for_incremental_reconnect(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(create_app(data_dir=tmp_path / "state", voices=(voice,), fixture_mode=True)) as client:
        asset = client.post("/v1/assets", content=_epub(), headers={"Content-Type": "application/epub+zip"}).json()
        book = client.get(f"/v1/assets/{asset['id']}/book").json()
        job = client.post(
            "/v1/jobs",
            json={"sourceId": asset["id"], "chapters": [book["chapters"][0]["id"]], "casting": {"voiceId": "narrator"}},
        ).json()
        for _ in range(50):
            if client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded":
                break
            __import__("time").sleep(0.02)

        stream = client.get(f"/v1/jobs/{job['id']}/events", headers={"Last-Event-ID": "1"})

        assert "id: 1\n" not in stream.text
        assert "id: 2\n" in stream.text
