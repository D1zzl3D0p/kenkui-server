"""Synthetic wire contracts consumed by the Neon port; no provider access."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

BASELINE = Path(__file__).parents[1] / "contracts/neon/baseline.json"


@pytest.mark.skipif(
    not os.environ.get("KENKUI_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL"
)
def test_contract_export_freezes_postgres_admission_and_payment_invariants():
    # Catches duplicate payment credits, non-atomic overspending, replayed jobs,
    # double settlement/refund, lost ownership, or missing SQL migrations.
    exporter = globals().get("export_database_contract")
    assert callable(exporter), "missing synthetic PostgreSQL invariant exporter"
    observed = exporter(os.environ["KENKUI_TEST_DATABASE_URL"])
    assert observed == json.loads(BASELINE.read_text())["postgresContract"]
    assert observed["creditsAfterPaymentReplay"] == 500
    assert observed["creditsAfterIdempotentAdmission"] == 300
    assert observed["concurrentAdmissions"] == {"accepted": 1, "rejected": 1}
    assert observed["creditsAfterFinalizationReplay"] == 200
    assert observed["authorizationStates"] == ["released", "settled"]
    assert observed["ownershipPreserved"] is True


def export_database_contract(url):
    """Migrate a new schema, exercise real SQL, then drop only that test schema."""
    from concurrent.futures import ThreadPoolExecutor
    from uuid import UUID, uuid4

    from kenkui_server.billing.models import AuthorizationStatus
    from kenkui_server.jobs.models import (
        Dispatch,
        Job,
        JobSpec,
        OutputSpec,
        SingleVoiceCasting,
        TtsSettings,
    )
    from kenkui_server.storage.postgres import PostgresHostedRepository, PostgresIdentityRepository
    from kenkui_server.storage.postgres_database import PostgresDatabase

    schema = "neon_contract_" + uuid4().hex
    admin = PostgresDatabase(url)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    db = PostgresDatabase(url, schema=schema)
    try:
        db.migrate()
        db.migrate()
        owner = PostgresIdentityRepository(db).user_id_for_subject("synthetic-contract-owner")
        account = str(UUID(int=100))
        db.execute(
            "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,0)",
            (account, str(owner)),
        )
        repo = PostgresHostedRepository(db)
        for _ in range(2):
            repo.billing.process_payment_event("stripe", "cs_contract", account, 500)
        payment_balance = repo.billing.account(account).available_credits
        spec = JobSpec(
            "source",
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("out.m4b"),
        )

        def admit(index, credits, key):
            job = Job(str(UUID(int=200 + index)), spec)
            return repo.admit(
                job,
                Dispatch(str(UUID(int=300 + index)), job.id, "pending"),
                account_id=account,
                owner_id=str(owner),
                credits=credits,
                idempotency_key=key,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            replay = list(pool.map(lambda i: admit(i, 200, "synthetic-replay"), range(2)))
        assert replay[0].id == replay[1].id
        admission_balance = repo.billing.account(account).available_credits

        def competing(index):
            try:
                return admit(index, 300, f"synthetic-competing-{index}")
            except ValueError as error:
                assert str(error) == "insufficient_credits"
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            competing_jobs = list(pool.map(competing, (2, 3)))
        accepted = [job for job in competing_jobs if job is not None]
        assert repo.billing.account(account).available_credits == 0
        for _ in range(2):
            repo.billing.finalize(replay[0].id, AuthorizationStatus.RELEASED)
            repo.billing.finalize(accepted[0].id, AuthorizationStatus.SETTLED)
        ledger = db.execute(
            "SELECT kind, count(*) AS count, sum(credits) AS credits FROM credit_ledger_entries "
            "GROUP BY kind ORDER BY kind"
        ).fetchall()
        return {
            "creditsAfterPaymentReplay": payment_balance,
            "creditsAfterIdempotentAdmission": admission_balance,
            "concurrentAdmissions": {"accepted": len(accepted), "rejected": 2 - len(accepted)},
            "creditsAfterFinalizationReplay": repo.billing.account(account).available_credits,
            "authorizationStates": sorted(
                row["status"]
                for row in db.execute("SELECT status FROM credit_authorizations").fetchall()
            ),
            "ownershipPreserved": all(
                repo.jobs.owner_id(job.id) == owner for job in (replay[0], accepted[0])
            ),
            "jobCount": db.execute("SELECT count(*) AS count FROM jobs").fetchone()["count"],
            "dispatchCount": db.execute("SELECT count(*) AS count FROM dispatches").fetchone()[
                "count"
            ],
            "ledger": [
                {"kind": r["kind"], "count": r["count"], "credits": int(r["credits"])}
                for r in ledger
            ],
            "migrations": [
                row["name"]
                for row in db.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
            ],
        }
    finally:
        db.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def export_contract(tmp_path, monkeypatch):
    """Run the real ASGI handlers; only external WorkOS and storage are synthetic."""
    import base64
    import hashlib
    import hmac
    import re
    from datetime import UTC, datetime
    from io import BytesIO
    from types import SimpleNamespace
    from unittest.mock import Mock
    from uuid import UUID
    from zipfile import ZipFile

    import kenkui as kk
    from fastapi.testclient import TestClient
    from test_hosted_composition import OWNER as HOSTED_OWNER
    from test_hosted_composition import _hosted_app
    from test_local_job_api import _epub

    from kenkui_server.app import create_app
    from kenkui_server.auth.browser import BrowserAuthBackend, BrowserSessionConfig
    from kenkui_server.auth.workos import InMemoryIdentityRepository
    from kenkui_server.billing import models as billing_models
    from kenkui_server.billing.models import InMemoryBillingRepository
    from kenkui_server.jobs.models import (
        Job,
        JobEvent,
        JobSpec,
        JobStatus,
        OutputSpec,
        Progress,
        SingleVoiceCasting,
        TtsSettings,
    )

    now = 1793642400
    owner = UUID(int=1)
    stranger = UUID(int=2)
    user = SimpleNamespace(id="contract-owner", email="owner@example.invalid", email_verified=True)
    other = SimpleNamespace(
        id="contract-stranger", email="other@example.invalid", email_verified=True
    )
    identities = InMemoryIdentityRepository()
    identities._identities.update({user.id: owner, other.id: stranger})
    management = Mock()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": now + 300}).encode()).decode().rstrip("=")
    grant = SimpleNamespace(
        user=user,
        sealed_session="synthetic-sealed",
        refresh_token="synthetic-refresh",
        access_token=f"header.{payload}.signature",
    )
    management.get_authorization_url.return_value = "https://auth.example.invalid/authorize"
    management.authenticate_with_code.return_value = grant
    management.authenticate_with_refresh_token.return_value = grant

    def session(*, sealed_session, cookie_password):
        authenticated = sealed_session in {"synthetic-sealed", "synthetic-stranger"}
        result = SimpleNamespace(
            authenticated=authenticated,
            user=other if sealed_session == "synthetic-stranger" else user,
            session_id="synthetic-session",
            reason="invalid_session",
        )
        return SimpleNamespace(
            authenticate=lambda: result,
            get_logout_url=lambda **_: "https://auth.example.invalid/logout",
        )

    management.load_sealed_session.side_effect = session
    backend = BrowserAuthBackend(
        SimpleNamespace(user_management=management),
        identities,
        BrowserSessionConfig(
            "https://api.example.invalid/v1/auth/callback",
            "https://studio.example.invalid",
            "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
            frozenset(),
            "http://127.0.0.1:43827/callback",
        ),
    )
    monkeypatch.setattr(
        "kenkui_server.auth.browser.secrets.token_urlsafe", lambda n: "s" * (43 if n == 32 else 64)
    )
    monkeypatch.setattr("kenkui_server.auth.native.time.time", lambda: now)
    monkeypatch.setattr("kenkui_server.api.assets.uuid4", lambda: UUID(int=3))
    monkeypatch.setattr("kenkui_server.app.uuid4", lambda: UUID(int=5))
    monkeypatch.setattr(billing_models, "uuid4", lambda: UUID(int=4))

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(now, tz=tz or UTC)

    monkeypatch.setattr(billing_models, "datetime", FrozenDatetime)
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    # Repack the existing tiny EPUB with stable ZIP timestamps.
    epub = BytesIO()
    with ZipFile(BytesIO(_epub())) as original, ZipFile(epub, "w") as archive:
        for name in original.namelist():
            from zipfile import ZipInfo

            archive.writestr(ZipInfo(name, (2020, 1, 1, 0, 0, 0)), original.read(name))
    http = []
    routes = {}

    def inventory(app, mode):
        for route in app.routes:
            if route.path.startswith("/v1/"):
                for method in sorted(route.methods or []):
                    key = (method, route.path)
                    routes.setdefault(key, {"method": method, "path": route.path, "modes": []})[
                        "modes"
                    ].append(mode)

    def call(client, name, method, path, *, route=None, headers=None, **kwargs):
        response = client.request(
            method,
            path,
            headers={"X-Request-ID": "contract-request", **(headers or {})},
            follow_redirects=False,
            **kwargs,
        )
        selected = {}
        for key in (
            "content-type",
            "cache-control",
            "pragma",
            "location",
            "x-request-id",
            "access-control-allow-origin",
            "access-control-allow-credentials",
            "access-control-allow-methods",
            "access-control-allow-headers",
            "vary",
            "set-cookie",
            "content-disposition",
        ):
            if key in response.headers:
                value = response.headers[key]
                # Expiry wall time is a test transport detail; all flags/values remain.
                selected[key] = re.sub(r"expires=[^;]+", "expires=<clock>", value, flags=re.I)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if path in {"/v1/openapi.json", "/v1/docs"}:
            body = {"sha256": hashlib.sha256(response.content).hexdigest()}
        request = {
            "path": path,
            "headers": {
                key: response.request.headers[key]
                for key in (
                    "content-type",
                    "authorization",
                    "cookie",
                    "origin",
                    "last-event-id",
                    "stripe-signature",
                    "x-request-id",
                    "access-control-request-method",
                    "access-control-request-headers",
                )
                if key in response.request.headers
            },
        }
        if "json" in kwargs:
            request["json"] = kwargs["json"]
        if "content" in kwargs:
            request["bodyBase64"] = base64.b64encode(kwargs["content"]).decode()
        record = {
            "name": name,
            "method": method,
            "route": route or path,
            "request": request,
            "status": response.status_code,
            "headers": selected,
            "body": body,
        }
        http.append(record)
        return response

    plain = create_app(data_dir=tmp_path / "local", voices=(voice,))
    inventory(plain, "local")
    with TestClient(plain, base_url="https://api.example.invalid") as client:
        source = call(
            client,
            "upload-epub",
            "POST",
            "/v1/assets",
            content=epub.getvalue(),
            headers={"Content-Type": "application/epub+zip"},
        ).json()
        asset_id = source["id"]
        call(
            client,
            "upload-pdf-unsupported",
            "POST",
            "/v1/assets",
            content=b"%PDF-1.4",
            headers={"Content-Type": "application/pdf"},
        )
        call(
            client,
            "upload-empty",
            "POST",
            "/v1/assets",
            content=b"",
            headers={"Content-Type": "application/epub+zip"},
        )
        book = call(
            client,
            "inspect-book",
            "GET",
            f"/v1/assets/{asset_id}/book",
            route="/v1/assets/{asset_id}/book",
        ).json()
        call(
            client,
            "cover-missing",
            "GET",
            f"/v1/assets/{asset_id}/cover",
            route="/v1/assets/{asset_id}/cover",
        )
        call(
            client,
            "cover-invalid",
            "POST",
            f"/v1/assets/{asset_id}/cover",
            route="/v1/assets/{asset_id}/cover",
            content=b"bad",
            headers={"Content-Type": "image/png"},
        )
        call(
            client,
            "preflight",
            "POST",
            "/v1/jobs/preflight",
            json={
                "sourceId": asset_id,
                "chapters": [book["chapters"][0]["id"]],
                "casting": {"voiceId": "narrator"},
            },
        )
        call(client, "job-invalid", "POST", "/v1/jobs", json={})

    app = create_app(
        data_dir=tmp_path / "local",
        voices=(voice,),
        auth_backend=backend,
        job_owner_resolver=lambda _: owner,
        asset_owner_resolver=lambda _: owner,
        allowed_origins=["https://studio.example.invalid"],
    )
    inventory(app, "browser-native")
    repositories = app.state.services.repositories
    spec = JobSpec(
        asset_id, ("one",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("out.m4b")
    )
    repositories.jobs.create(
        Job("contract-job", spec, JobStatus.SUCCEEDED, 2, Progress("complete", 1, 1))
    )
    repositories.events.append(JobEvent("contract-job", 1, "queued", Progress("queued", 0, 1)))
    repositories.events.append(JobEvent("contract-job", 2, "succeeded", Progress("complete", 1, 1)))
    auth = {"Authorization": "Bearer synthetic-sealed"}
    with TestClient(app, base_url="https://api.example.invalid") as client:
        for path in (
            "/v1/health",
            "/v1/capabilities",
            "/v1/voices",
            "/v1/openapi.json",
            "/v1/docs",
        ):
            call(client, path.rsplit("/", 1)[-1], "GET", path)
        # Starlette advertises HEAD on these documentation helpers too.
        for path in ("/v1/openapi.json", "/v1/docs"):
            call(client, "head-" + path.rsplit("/", 1)[-1], "HEAD", path)
        call(client, "missing-auth", "GET", "/v1/jobs")
        call(client, "invalid-auth", "GET", "/v1/jobs", headers={"Authorization": "Bearer invalid"})
        call(
            client,
            "cors-options",
            "OPTIONS",
            "/v1/jobs",
            headers={
                "Origin": "https://studio.example.invalid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            },
        )
        call(
            client,
            "cors-missing-auth",
            "GET",
            "/v1/jobs",
            headers={"Origin": "https://studio.example.invalid"},
        )
        call(
            client,
            "foreign-asset",
            "GET",
            f"/v1/assets/{asset_id}/book",
            route="/v1/assets/{asset_id}/book",
            headers={"Authorization": "Bearer synthetic-stranger"},
        )
        call(client, "jobs", "GET", "/v1/jobs", headers=auth)
        call(client, "job", "GET", "/v1/jobs/contract-job", route="/v1/jobs/{job_id}", headers=auth)
        call(
            client,
            "foreign-job",
            "GET",
            "/v1/jobs/contract-job",
            route="/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer synthetic-stranger"},
        )
        call(
            client,
            "cancel-terminal",
            "POST",
            "/v1/jobs/contract-job/cancel",
            route="/v1/jobs/{job_id}/cancel",
            headers=auth,
        )
        call(
            client,
            "sse-resume",
            "GET",
            "/v1/jobs/contract-job/events",
            route="/v1/jobs/{job_id}/events",
            headers={**auth, "Last-Event-ID": "1"},
        )
        call(
            client,
            "artifact-missing",
            "GET",
            "/v1/jobs/contract-job/artifact",
            route="/v1/jobs/{job_id}/artifact",
            headers=auth,
        )
        call(
            client,
            "unsubscribe-invalid",
            "GET",
            "/v1/notifications/unsubscribe?identity=" + str(owner) + "&token=invalid",
            route="/v1/notifications/unsubscribe",
        )
        call(client, "notification-get", "GET", "/v1/notifications", headers=auth)
        call(
            client,
            "notification-patch",
            "PATCH",
            "/v1/notifications",
            headers=auth,
            json={"emailOnCompletion": True},
        )
        call(client, "browser-login", "GET", "/v1/auth/login")
        call(
            client,
            "browser-callback-invalid",
            "GET",
            "/v1/auth/callback?code=synthetic&state=bad",
            route="/v1/auth/callback",
        )
        call(
            client,
            "browser-callback",
            "GET",
            "/v1/auth/callback?code=synthetic&state=" + "s" * 43,
            route="/v1/auth/callback",
        )
        call(client, "browser-session", "GET", "/v1/auth/session")
        call(
            client,
            "browser-csrf",
            "POST",
            "/v1/auth/logout",
            headers={"Origin": "https://evil.invalid"},
        )
        call(
            client,
            "browser-logout",
            "POST",
            "/v1/auth/logout",
            headers={"Origin": "https://studio.example.invalid"},
        )
        call(client, "native-config", "GET", "/v1/auth/native/config")
        call(
            client,
            "native-authorize",
            "GET",
            "/v1/auth/native/authorize?state=" + "s" * 43 + "&code_challenge=" + "c" * 43,
            route="/v1/auth/native/authorize",
        )
        call(
            client,
            "native-exchange",
            "POST",
            "/v1/auth/native/exchange",
            json={"code": "synthetic-code", "codeVerifier": "v" * 43},
        )
        call(
            client,
            "native-invalid",
            "POST",
            "/v1/auth/native/exchange",
            json={"code": "synthetic-code", "codeVerifier": "short"},
        )
        call(
            client,
            "native-refresh",
            "POST",
            "/v1/auth/native/refresh",
            json={"refreshToken": "synthetic-refresh"},
        )
        call(client, "native-logout", "POST", "/v1/auth/native/logout", headers=auth)

    hosted, fixture = _hosted_app()
    billing = InMemoryBillingRepository()
    fixture.repositories.billing = billing
    # Build the handler after installing its real in-memory billing repository.
    # The external fixture already supplies storage/auth/dispatch boundaries.
    from dataclasses import replace

    hosted = create_app(
        hosted_config=hosted.state.hosted_config,
        hosted_services=replace(
            hosted.state.hosted_services,
            account_id_for_identity=lambda identity_id: (
                "account-1" if identity_id == HOSTED_OWNER else "account-2"
            ),
        ),
    )
    inventory(hosted, "hosted")
    with TestClient(hosted) as client:
        event = {
            "id": "evt_contract",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_contract",
                    "mode": "payment",
                    "client_reference_id": "account-1",
                    "payment_status": "paid",
                    "amount_total": 500,
                    "amount_subtotal": 500,
                    "currency": "usd",
                    "total_details": {"amount_tax": 0, "amount_discount": 0, "amount_shipping": 0},
                    "metadata": {
                        "purpose": "kenkui_credits_v1",
                        "account_id": "account-1",
                        "credits": "500",
                    },
                }
            },
        }
        raw = json.dumps(event, separators=(",", ":")).encode()
        signature = hmac.new(
            b"stripe-secret", str(now).encode() + b"." + raw, hashlib.sha256
        ).hexdigest()
        call(
            client,
            "stripe-invalid-signature",
            "POST",
            "/v1/billing/webhooks/stripe",
            content=raw,
            headers={"Stripe-Signature": "bad"},
        )
        for name in ("stripe-paid", "stripe-replay"):
            call(
                client,
                name,
                "POST",
                "/v1/billing/webhooks/stripe",
                content=raw,
                headers={"Stripe-Signature": f"t={now},v1={signature}"},
            )
        for name, path in (("billing", "/v1/billing"), ("credit-history", "/v1/billing/history")):
            call(client, name, "GET", path, headers={"Authorization": "Bearer owner"})
        call(
            client,
            "foreign-credit-history",
            "GET",
            "/v1/billing/history",
            headers={"Authorization": "Bearer stranger"},
        )
        call(
            client,
            "checkout-unavailable",
            "POST",
            "/v1/billing/checkout",
            headers={"Authorization": "Bearer owner"},
            json={"credits": 250},
        )

    return {
        "clock": "2026-11-02T18:00:00Z",
        "routes": [routes[key] for key in sorted(routes)],
        "http": http,
        "database": {"paymentCreditsAfterReplay": billing.account("account-1").available_credits},
        "schema": app.openapi(),
        "hostedSchema": hosted.openapi(),
    }


def test_contract_export_covers_auth_webhook_and_v1(tmp_path, monkeypatch):
    # Catches dropping a registered route or changing observable auth/ownership,
    # camelCase payloads, webhook idempotency, errors, headers or SSE resume IDs.
    exporter = globals().get("export_contract")
    assert callable(exporter), "missing deterministic synthetic contract exporter"
    import kenkui as kk

    frozen = json.loads(BASELINE.read_text())
    core_root = Path(kk.__file__).resolve().parents[2]
    actual_core = subprocess.check_output(
        ["rtk", "proxy", "git", "-C", str(core_root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert actual_core == frozen["repositories"]["core"]["sha"], (
        "use the immutable matching core checkout"
    )
    observed = exporter(tmp_path, monkeypatch)
    assert observed == frozen["contract"]
    routes = {(r["method"], r["path"]) for r in observed["routes"]}
    exercised = {(r["method"], r["route"]) for r in observed["http"]}
    assert routes <= exercised
    assert ("GET", "/v1/auth/login") in routes
    assert ("POST", "/v1/auth/native/exchange") in routes
    assert ("POST", "/v1/billing/webhooks/stripe") in routes
    cases = {case["name"]: case for case in observed["http"]}
    assert cases["native-config"]["body"] == {"redirectUri": "http://127.0.0.1:43827/callback"}
    assert cases["native-exchange"]["status"] == 200
    assert cases["native-exchange"]["body"]["expiresIn"] == 300
    assert cases["browser-session"]["body"] == {"userId": "00000000-0000-0000-0000-000000000001"}
    assert cases["browser-callback-invalid"]["status"] == 400
    assert cases["browser-csrf"]["status"] == 403
    assert cases["foreign-asset"]["status"] == 403
    assert (
        cases["cors-missing-auth"]["headers"]["access-control-allow-origin"]
        == "https://studio.example.invalid"
    )
    assert cases["billing"]["body"]["availableCredits"] == "500"
    assert len(cases["credit-history"]["body"]["items"]) == 1
    assert cases["foreign-credit-history"]["body"]["items"] == []
    assert cases["native-exchange"]["headers"]["cache-control"] == "no-store"
    assert cases["missing-auth"]["status"] == 401
    assert cases["foreign-job"]["status"] == 403
    assert cases["stripe-invalid-signature"]["status"] == 400
    assert cases["stripe-replay"]["status"] == 204
    assert observed["database"]["paymentCreditsAfterReplay"] == 500
    assert "id: 1\n" not in cases["sse-resume"]["body"]
    assert "id: 2\n" in cases["sse-resume"]["body"]
