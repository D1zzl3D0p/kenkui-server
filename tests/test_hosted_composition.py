from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from kenkui_server.api import jobs
from kenkui_server.app import HostedServices, create_app
from kenkui_server.auth.base import Identity
from kenkui_server.config import HostedConfig
from kenkui_server.jobs.models import (
    Asset,
    CharacterCasting,
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
            "data": {
                "object": {
                    "id": "cs_1",
                    "mode": "payment",
                    "client_reference_id": "account-1",
                    "payment_status": "paid",
                    "currency": "usd",
                    "amount_total": 500,
                    "amount_subtotal": 500,
                    "total_details": {"amount_tax": 0, "amount_discount": 0, "amount_shipping": 0},
                    "metadata": {
                        "purpose": "kenkui_credits_v1",
                        "account_id": "account-1",
                        "credits": "500",
                    },
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(
        b"stripe-secret", timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()

    with TestClient(app) as client:
        response = client.post(
            "/v1/billing/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": f"t={timestamp},v1={digest}"},
        )

    assert response.status_code == 204
    assert app.state.services.repositories is fixture.repositories
    assert fixture.repositories.billing.events == [("stripe", "cs_1", "account-1", 500)]


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
    assert (account_id, owner_id, credits, key) == (
        "account-1",
        str(OWNER),
        1,
        hashlib.sha256(f"{OWNER}:request-1".encode()).hexdigest(),
    )
    assert fixture.runner.started == [admitted_dispatch.id]


@pytest.mark.parametrize("multivoice", [False, True])
@pytest.mark.parametrize("characters", [1, 1_189_736, 3_238_498, 10_000_000])
def test_preflight_and_admission_agree_on_estimated_book_charge(
    monkeypatch, characters, multivoice
):
    app, fixture = _hosted_app()
    spec = JobSpec(
        "source-1",
        ("chapter-1",),
        SingleVoiceCasting("narrator"),
        TtsSettings(),
        OutputSpec("artifact.m4b"),
    )
    if multivoice:
        from dataclasses import replace

        spec = replace(spec, casting=CharacterCasting("narrator", "narrator", (), "model", "model"))
    monkeypatch.setattr(jobs, "_preflight", lambda request, payload: (spec, characters))
    fixture.repositories.billing.account = lambda _: SimpleNamespace(available_credits=10000)
    payload = {
        "sourceId": "source-1",
        "chapters": ["chapter-1"],
        "casting": {"voiceId": "narrator"},
        "output": {"format": "m4b"},
    }
    with TestClient(app) as client:
        preflight = client.post(
            "/v1/jobs/preflight", headers={"Authorization": "Bearer owner"}, json=payload
        )
        assert preflight.status_code == 200
        assert (
            preflight.json()["estimatedCredits"]
            == (characters * (378 if multivoice else 252) + 999_999) // 1_000_000
        )
        assert preflight.json()["valid"] is True
        admitted = client.post("/v1/jobs", headers={"Authorization": "Bearer owner"}, json=payload)
        assert admitted.status_code == 202
    assert (
        fixture.repositories.admissions[0][4]
        == (characters * (378 if multivoice else 252) + 999_999) // 1_000_000
    )


def test_insufficient_balance_rejects_cost_based_preflight(monkeypatch):
    app, fixture = _hosted_app()
    spec = JobSpec(
        "source-1",
        ("chapter-1",),
        SingleVoiceCasting("narrator"),
        TtsSettings(),
        OutputSpec("artifact.m4b"),
    )
    monkeypatch.setattr(jobs, "_preflight", lambda request, payload: (spec, 1_189_736))
    fixture.repositories.billing.account = lambda _: SimpleNamespace(available_credits=299)
    with TestClient(app) as client:
        result = client.post(
            "/v1/jobs/preflight",
            headers={"Authorization": "Bearer owner"},
            json={
                "sourceId": "source-1",
                "chapters": ["chapter-1"],
                "casting": {"voiceId": "narrator"},
            },
        )
    assert result.json()["valid"] is False
    assert fixture.repositories.admissions == []


def test_checkout_requires_auth_and_uses_callers_account():
    app, _ = _hosted_app()
    calls = []
    app.state.stripe_checkout = SimpleNamespace(
        create=lambda account, credits: (
            calls.append((account, credits)) or "https://checkout.stripe.com/test"
        )
    )
    with TestClient(app) as client:
        assert client.post("/v1/billing/checkout", json={"credits": 500}).status_code == 401
        headers = {"Authorization": "Bearer owner"}
        for payload in [
            {"credits": 1},
            {"credits": "500"},
            {"credits": 500, "account_id": "stranger"},
        ]:
            assert (
                client.post("/v1/billing/checkout", json=payload, headers=headers).status_code
                == 422
            )
        result = client.post("/v1/billing/checkout", json={"credits": 500}, headers=headers)
        assert result.status_code == 200
        assert calls == [("account-1", 500)]
        app.state.stripe_checkout = None
        assert (
            client.post("/v1/billing/checkout", json={"credits": 500}, headers=headers).status_code
            == 503
        )


def test_billing_publishes_pack_prices_and_accepts_the_published_quantities():
    app, fixture = _hosted_app()
    fixture.repositories.billing.account = lambda _: SimpleNamespace(available_credits=0)
    calls = []
    app.state.stripe_checkout = SimpleNamespace(
        create=lambda account, credits: (
            calls.append((account, credits)) or "https://checkout.stripe.com/test"
        )
    )
    headers = {"Authorization": "Bearer owner"}
    with TestClient(app) as client:
        response = client.get("/v1/billing", headers=headers)
        assert response.status_code == 200
        packs = response.json()["packs"]
        assert packs == [
            {"credits": 500, "priceUsdCents": 500},
            {"credits": 1100, "priceUsdCents": 1000},
            {"credits": 2400, "priceUsdCents": 2000},
        ]
        for pack in packs:
            assert (
                client.post(
                    "/v1/billing/checkout", headers=headers, json={"credits": pack["credits"]}
                ).status_code
                == 200
            )
        assert (
            client.post("/v1/billing/checkout", headers=headers, json={"credits": 1000}).status_code
            == 422
        )
    assert calls == [("account-1", 500), ("account-1", 1100), ("account-1", 2400)]


def test_credit_history_is_scoped_to_authenticated_account():
    from kenkui_server.billing.models import AuthorizationStatus, InMemoryBillingRepository

    app, fixture = _hosted_app()
    ledger = InMemoryBillingRepository()
    ledger.process_payment_event("stripe", "owner-pack", "account-1", 500)
    ledger.process_payment_event("stripe", "other-pack", "account-2", 2400)
    ledger.reserve("pending", "account-1", 20)
    fixture.repositories.billing = ledger
    with TestClient(app) as client:
        assert client.get("/v1/billing/history").status_code == 401
        response = client.get(
            "/v1/billing/history?account_id=account-2", headers={"Authorization": "Bearer owner"}
        )
        assert response.status_code == 200
        (pack,) = response.json()["items"]
        assert pack["reference"] == "stripe:owner-pack"
        assert (pack["credited"], pack["available"], pack["reserved"], pack["consumed"]) == (
            500,
            480,
            20,
            0,
        )
        assert pack["usageStatus"] == "reserved"
        ledger.finalize("pending", AuthorizationStatus.RELEASED)
        assert (
            client.get("/v1/billing/history", headers={"Authorization": "Bearer owner"}).json()[
                "items"
            ][0]["usageStatus"]
            == "unused"
        )
