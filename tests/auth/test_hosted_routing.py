from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from kenkui_server.app import create_app
from kenkui_server.auth.base import Identity
from kenkui_server.jobs.models import (
    Job,
    JobSpec,
    OutputSpec,
    Progress,
    SingleVoiceCasting,
    TtsSettings,
)


class RecordingAuthBackend:
    def __init__(self, identity: Identity) -> None:
        self.identity = identity
        self.tokens: list[str] = []
        self.authorizations: list[tuple[UUID, UUID]] = []

    def authenticate(self, session_token: str) -> Identity:
        self.tokens.append(session_token)
        return self.identity

    def authorize(self, actor_id: UUID, owner_id: UUID) -> bool:
        self.authorizations.append((actor_id, owner_id))
        return actor_id == owner_id


def _job() -> Job:
    return Job(
        "job-1",
        JobSpec(
            source_id="source-1",
            chapters=("chapter-1",),
            casting=SingleVoiceCasting("voice-1"),
            tts=TtsSettings(),
            output=OutputSpec("private.m4b"),
        ),
        progress=Progress("queued", 0, 1),
    )


def test_hosted_job_route_authenticates_session_and_enforces_resource_owner(tmp_path: Path) -> None:
    owner = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    stranger = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    backend = RecordingAuthBackend(Identity(stranger, "user-2"))
    app = create_app(
        data_dir=tmp_path / "state",
        auth_backend=backend,
        job_owner_resolver=lambda job_id: owner,
    )
    app.state.local_services.repositories.jobs.create(_job())

    with TestClient(app) as client:
        missing = client.get("/v1/jobs/job-1")
        forbidden = client.get("/v1/jobs/job-1", headers={"Authorization": "Bearer session-2"})

    assert missing.status_code == 401
    assert forbidden.status_code == 403
    assert backend.tokens == ["session-2"]
    assert backend.authorizations == [(stranger, owner)]


def test_browser_receives_auth_errors_and_preflight_without_a_session(tmp_path: Path) -> None:
    owner, stranger = UUID(int=1), UUID(int=2)
    origin = "https://app.kenkui.fm"
    app = create_app(
        data_dir=tmp_path,
        auth_backend=RecordingAuthBackend(Identity(stranger, "stranger")),
        job_owner_resolver=lambda _: owner,
        allowed_origins=[origin],
    )
    app.state.local_services.repositories.jobs.create(_job())
    with TestClient(app) as client:
        missing = client.get("/v1/jobs/job-1", headers={"Origin": origin})
        forbidden = client.get(
            "/v1/jobs/job-1",
            headers={
                "Origin": origin,
                "Authorization": "Bearer stranger",
            },
        )
        preflight = client.options(
            "/v1/jobs",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,idempotency-key",
            },
        )
    assert [missing.status_code, forbidden.status_code, preflight.status_code] == [401, 403, 200]
    for response in (missing, forbidden, preflight):
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
