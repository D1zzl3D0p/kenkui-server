from types import SimpleNamespace

from fastapi.testclient import TestClient

from kenkui_server.compute.api_bridge import create_bridge_app
from kenkui_server.notifications.mailer import RecordingMailer


def test_protected_ready_needs_no_database_and_invalid_requests_echo_nothing():
    mailer = RecordingMailer()
    app = create_bridge_app(
        environment="staging",
        secret="test-only",
        services_factory=lambda: 1 / 0,
        migration_mailer=lambda: mailer,
    )
    auth = {"X-Kenkui-Bridge-Secret": "test-only"}
    with TestClient(app) as client:
        assert (
            client.post("/migration-email/ready", json={"environment": "staging"}).status_code
            == 401
        )
        assert (
            client.post(
                "/migration-email/ready", headers=auth, json={"environment": "production"}
            ).status_code
            == 403
        )
        assert client.post(
            "/migration-email/ready", headers=auth, json={"environment": "staging"}
        ).json() == {"ready": True}
        for body in [
            {"environment": "staging", "email": "secret@private.test"},
            {"code": "secret-123"},
            # A valid claim envelope cannot add a fixture subject, owner or recipient override.
            {
                "environment": "staging",
                "challengeId": "00000000-0000-4000-8000-000000000001",
                "code": "0123456789ABCDEF",
                "legacySubject": "secret-fixture",
                "recipient": "secret@example.test",
            },
        ]:
            response = client.post("/migration-email", headers=auth, json=body)
            assert response.status_code == 422 and "secret" not in response.text
        assert mailer.sent == []


def test_unconfigured_sender_fails_before_services():
    def disabled():
        raise ValueError("secret account")

    app = create_bridge_app(
        environment="staging",
        secret="test-only",
        services_factory=SimpleNamespace,
        migration_mailer=disabled,
    )
    with TestClient(app) as client:
        response = client.post(
            "/migration-email/ready",
            headers={"X-Kenkui-Bridge-Secret": "test-only"},
            json={"environment": "staging"},
        )
        assert response.status_code == 503 and "secret" not in response.text
