from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from kenkui_server.api import jobs
from kenkui_server.app import HostedServices, create_app
from kenkui_server.auth.base import Identity
from kenkui_server.config import HostedConfig
from kenkui_server.jobs.models import (
    Asset,
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)

OWNER = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
STRANGER = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class Auth:
    def authenticate(self, token: str) -> Identity:
        if token == "owner":
            return Identity(OWNER, "owner")
        if token == "stranger":
            return Identity(STRANGER, "stranger")
        raise PermissionError("unauthenticated")

    def authorize(self, actor_id: UUID, owner_id: UUID) -> bool:
        return actor_id == owner_id


class Assets:
    def __init__(self) -> None:
        self.asset = Asset("source-1", "/private/source-1.epub", "digest", "epub")

    def get(self, asset_id: str) -> Asset:
        if asset_id != self.asset.id:
            raise KeyError(asset_id)
        return self.asset

    def owner_id(self, asset_id: str) -> UUID:
        self.get(asset_id)
        return OWNER


class Dispatches:
    def list_incomplete(self) -> tuple[Dispatch, ...]:
        return ()


class Jobs:
    def get(self, job_id: str) -> Job:
        raise KeyError(job_id)

    def list(self) -> tuple[Job, ...]:
        return ()

    def owner_id(self, job_id: str) -> UUID:
        raise KeyError(job_id)


class Billing:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, int]] = []

    def process_payment_event(
        self, provider: str, provider_event_id: str, account_id: str, credits: int
    ) -> object:
        self.events.append((provider, provider_event_id, account_id, credits))
        return object()


class AtomicHostedRepositories:
    def __init__(self) -> None:
        self.assets = Assets()
        self.jobs = Jobs()
        self.dispatches = Dispatches()
        self.billing = Billing()
        self.admissions: list[tuple[Job, Dispatch, str, str, int, str | None]] = []

    def admit(
        self,
        job: Job,
        dispatch: Dispatch,
        *,
        account_id: str,
        owner_id: str,
        credits: int,
        idempotency_key: str | None,
    ) -> Job:
        self.admissions.append((job, dispatch, account_id, owner_id, credits, idempotency_key))
        return job


class Runner:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, dispatch_id: str) -> None:
        self.started.append(dispatch_id)


@dataclass
class HostedFixture:
    repositories: AtomicHostedRepositories
    runner: Runner


def _config() -> HostedConfig:
    return HostedConfig(
        database_url="postgresql://db",
        r2_bucket="private",
        r2_endpoint="https://r2.example",
        r2_access_key_id="key",
        r2_secret_access_key="r2-secret",
        workos_api_key="workos-secret",
        stripe_webhook_secret="stripe-secret",
    )


def _hosted_app() -> tuple[object, HostedFixture]:
    repositories = AtomicHostedRepositories()
    runner = Runner()
    app = create_app(
        hosted_config=_config(),
        hosted_services=HostedServices(
            repositories=repositories,
            assets=object(),
            voices=(),
            runner=runner,
            auth_backend=Auth(),
            account_id_for_identity=lambda identity_id: "account-1",
        ),
    )
    return app, HostedFixture(repositories, runner)


def test_hosted_config_composes_durable_services_and_installs_stripe_webhook() -> None:
    app, fixture = _hosted_app()
    payload = json.dumps(
        {
            "id": "evt-1",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"account_id": "account-1", "credits": "4"}}},
        },
        separators=(",", ":"),
    ).encode()
    digest = hmac.new(b"stripe-secret", b"123." + payload, hashlib.sha256).hexdigest()

    with TestClient(app) as client:
        response = client.post(
            "/v1/billing/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": f"t=123,v1={digest}"},
        )

    assert response.status_code == 204
    assert app.state.services.repositories is fixture.repositories
    assert fixture.repositories.billing.events == [("stripe", "evt-1", "account-1", 4)]


def test_hosted_assets_require_authentication_and_owner_access() -> None:
    app, _ = _hosted_app()

    with TestClient(app) as client:
        missing = client.get("/v1/assets/source-1/book")
        forbidden = client.get(
            "/v1/assets/source-1/book", headers={"Authorization": "Bearer stranger"}
        )

    assert missing.status_code == 401
    assert forbidden.status_code == 403


def test_hosted_job_route_uses_single_atomic_durable_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, fixture = _hosted_app()
    spec = JobSpec(
        "source-1",
        ("chapter-1",),
        SingleVoiceCasting("narrator"),
        TtsSettings(),
        OutputSpec("artifact.m4b"),
    )
    monkeypatch.setattr(jobs, "_preflight", lambda request, payload: (spec, 1_001))

    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer owner", "Idempotency-Key": "request-1"},
            json={
                "sourceId": "source-1",
                "chapters": ["chapter-1"],
                "casting": {"voiceId": "narrator"},
                "output": {"format": "m4b"},
            },
        )

    assert response.status_code == 202
    assert len(fixture.repositories.admissions) == 1
    (
        admitted_job,
        admitted_dispatch,
        account_id,
        owner_id,
        credits,
        key,
    ) = fixture.repositories.admissions[0]
    assert admitted_dispatch.job_id == admitted_job.id
    assert (account_id, owner_id, credits, key) == ("account-1", str(OWNER), 2, hashlib.sha256(f"{OWNER}:request-1".encode()).hexdigest())
    assert fixture.runner.started == [admitted_dispatch.id]
