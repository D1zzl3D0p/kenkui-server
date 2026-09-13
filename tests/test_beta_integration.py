import os
from pathlib import Path
from uuid import UUID

import kenkui as kk
import pytest
from fastapi.testclient import TestClient

from kenkui_server.app import create_app
from kenkui_server.auth.base import Identity
from kenkui_server.jobs.models import (
    Artifact,
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.jobs.transitions import Completed, DispatchRequested, transition
from kenkui_server.storage.assets import FakeS3Client, R2AssetStore
from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories

WEB_ROOT = Path(
    os.environ.get("KENKUI_WEB_ROOT") or Path(__file__).resolve().parents[2] / "kenkui-studio"
)
SOURCE = WEB_ROOT / "tests/fixtures/book.epub"


def test_upload_limit_is_enforced_before_storage(tmp_path):
    app = create_app(data_dir=tmp_path, voices=(), max_upload_bytes=4)
    with TestClient(app) as client:
        response = client.post(
            "/v1/assets", content=b"12345", headers={"Content-Type": "application/epub+zip"}
        )
    assert response.status_code == 413
    assert list((tmp_path / "assets/sources").iterdir()) == []


def test_cross_owner_preflight_and_creation_are_denied(tmp_path):
    owner, stranger = UUID(int=1), UUID(int=2)

    class Auth:
        def authenticate(self, token):
            return Identity(stranger, "stranger")

        def authorize(self, actor, resource_owner):
            return actor == resource_owner

    app = create_app(
        data_dir=tmp_path,
        voices=(),
        auth_backend=Auth(),
        job_owner_resolver=lambda _: owner,
        asset_owner_resolver=lambda _: owner,
    )
    services = app.state.services
    from kenkui_server.jobs.models import Asset

    path = services.assets.put_source("source", SOURCE.read_bytes())
    services.repositories.assets.put(Asset("source", str(path), "digest", "epub"))
    with TestClient(app) as client:
        for endpoint in ("/v1/jobs/preflight", "/v1/jobs"):
            response = client.post(
                endpoint,
                headers={"Authorization": "Bearer stranger"},
                json={
                    "sourceId": "source",
                    "chapters": ["chapter"],
                    "casting": {"voiceId": "narrator"},
                },
            )
            assert response.status_code == 403
    assert services.repositories.jobs.list() == ()


def test_r2_source_materialization_supports_library_inspection_and_cleans_up():
    store = R2AssetStore(FakeS3Client(), bucket="private")
    store.put_source("source", SOURCE.read_bytes())
    with store.materialize_source("source") as path:
        assert kk.book(path).inspect().chapters
    assert not path.exists()


def test_completion_rolls_back_if_artifact_registration_fails(tmp_path):
    database = Database(tmp_path / "state.sqlite3")
    repo = Repositories(database)
    spec = JobSpec(
        "source", ("chapter",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("out.m4b")
    )
    running = transition(Job("job", spec), DispatchRequested())
    repo.create_job_and_dispatch(
        running, Dispatch("dispatch", "job", "running"), idempotency_key=None
    )
    # A database failure after updating the job must roll back both state and event.
    database.connection.execute(
        "CREATE TRIGGER reject_artifact BEFORE INSERT ON artifacts BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
    )
    completed = transition(running, Completed())
    with pytest.raises(Exception, match="disk failure"):
        repo.update_job_and_append_event(
            completed,
            expected_version=running.version,
            event_type="completed",
            artifact=Artifact("artifact", "job", "out.m4b", "m4b"),
        )
    assert repo.jobs.get("job") == running
    assert repo.events.list_for_job("job") == ()
    database.close()


def test_default_voice_discovery_only_advertises_loaded_voices(tmp_path, monkeypatch):
    from dataclasses import replace

    voice = kk.Voice("narrator", "Narrator", True, "test", "test", True)
    monkeypatch.setattr(
        kk, "list_voices", lambda: (voice, replace(voice, id="loaded", state="loaded"))
    )
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert [v["id"] for v in client.get("/v1/voices").json()["items"]] == ["loaded"]
