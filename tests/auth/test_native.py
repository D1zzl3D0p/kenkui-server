import base64
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from kenkui_server.app import create_app
from kenkui_server.auth.browser import BrowserAuthBackend, BrowserSessionConfig
from kenkui_server.auth.workos import InMemoryIdentityRepository

REDIRECT = "http://127.0.0.1:43827/callback"


@pytest.fixture
def native(tmp_path):
    user = SimpleNamespace(id="user", email="invited@example.com", email_verified=True)
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + 300}).encode())
    management = Mock()
    grant = SimpleNamespace(
        user=user,
        sealed_session="sealed",
        refresh_token="refresh",
        access_token=f"header.{payload.decode().rstrip('=')}.signature",
    )
    management.authenticate_with_code.return_value = grant
    management.authenticate_with_refresh_token.return_value = grant
    management.get_authorization_url.return_value = "https://auth.example.com/login"
    management.load_sealed_session.return_value.authenticate.return_value = SimpleNamespace(
        authenticated=True, user=user, session_id="session_1"
    )
    config = BrowserSessionConfig(
        "https://api.example.com/v1/auth/callback",
        "https://app.example.com",
        "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        frozenset({user.email}),
        REDIRECT,
    )
    backend = BrowserAuthBackend(
        SimpleNamespace(user_management=management), InMemoryIdentityRepository(), config
    )
    app = create_app(
        data_dir=tmp_path, voices=(), auth_backend=backend, job_owner_resolver=lambda _: UUID(int=1)
    )
    with TestClient(app, base_url="https://api.example.com") as client:
        yield client, backend, management


def test_native_config_and_authorize_fix_the_redirect_and_require_pkce(native):
    client, _, management = native
    response = client.get("/v1/auth/native/config")
    assert response.json() == {"redirectUri": REDIRECT}
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/v1/auth/native/authorize").status_code == 422
    response = client.get(
        "/v1/auth/native/authorize",
        params={
            "state": "a" * 43,
            "code_challenge": "b" * 43,
            "redirect_uri": "https://attacker.example/callback",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "set-cookie" not in response.headers
    management.get_authorization_url.assert_called_once_with(
        provider="authkit",
        redirect_uri=REDIRECT,
        state="a" * 43,
        code_challenge="b" * 43,
    )


def test_exchange_checks_pkce_and_returns_native_credentials_without_cookies(native):
    client, backend, management = native
    assert client.post("/v1/auth/native/exchange", json={"code": "code"}).status_code == 422
    assert (
        client.post(
            "/v1/auth/native/exchange", json={"code": "code", "codeVerifier": "short"}
        ).status_code
        == 422
    )
    management.authenticate_with_code.assert_not_called()
    response = client.post(
        "/v1/auth/native/exchange",
        json={
            "code": "code",
            "codeVerifier": "a" * 43,
        },
    )
    assert response.status_code == 200
    assert response.json()["accessToken"] == "sealed"
    assert response.json()["refreshToken"] == "refresh"
    assert 0 < response.json()["expiresIn"] <= 300
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    management.authenticate_with_code.assert_called_once_with(
        code="code",
        code_verifier="a" * 43,
        session={"seal_session": True, "cookie_password": backend.config.cookie_password},
    )
    who = client.get("/v1/auth/session", headers={"Authorization": "Bearer sealed"})
    assert who.status_code == 200
    assert who.json()["userId"] == response.json()["userId"]


def test_refresh_rotates_and_rechecks_invitation(native):
    client, _, management = native
    result = management.authenticate_with_refresh_token.return_value
    result.sealed_session, result.refresh_token = "new-access", "new-refresh"
    response = client.post("/v1/auth/native/refresh", json={"refreshToken": "old-refresh"})
    assert response.status_code == 200
    assert response.json()["refreshToken"] == "new-refresh"
    assert (
        management.authenticate_with_refresh_token.call_args.kwargs["refresh_token"]
        == "old-refresh"
    )
    result.user.email_verified = False
    assert client.post("/v1/auth/native/refresh", json={"refreshToken": "old"}).status_code == 403


def test_expired_bearer_does_not_implicitly_rotate(native):
    client, _, management = native
    session = management.load_sealed_session.return_value
    session.authenticate.return_value = SimpleNamespace(authenticated=False, reason="invalid_jwt")
    response = client.get("/v1/auth/session", headers={"Authorization": "Bearer expired"})
    assert response.status_code == 401
    session.refresh.assert_not_called()


def test_logout_requires_bearer_and_revokes_provider_session(native):
    client, _, management = native
    assert client.post("/v1/auth/native/logout").status_code == 401
    response = client.post("/v1/auth/native/logout", headers={"Authorization": "Bearer sealed"})
    assert response.status_code == 204
    management.revoke_session.assert_called_once_with(session_id="session_1")


def test_disabled_native_auth_and_disallowed_redirect(native):
    client, backend, _ = native
    backend.config = replace(backend.config, native_redirect_uri=None)
    assert client.get("/v1/auth/native/config").status_code == 404
    assert (
        client.post(
            "/v1/auth/native/exchange", json={"code": "x", "codeVerifier": "a" * 43}
        ).status_code
        == 404
    )
    with pytest.raises(ValueError, match="native redirect"):
        replace(backend.config, native_redirect_uri="https://attacker.example")


@pytest.mark.parametrize(
    "status, expected", [(400, 401), (401, 401), (403, 401), (429, 503), (500, 503)]
)
def test_refresh_errors_are_sanitized_and_transient_failures_distinct(native, status, expected):
    client, _, management = native
    error = Exception("secret-provider-credential")
    error.response = SimpleNamespace(status_code=status)
    management.authenticate_with_refresh_token.side_effect = error
    response = client.post("/v1/auth/native/refresh", json={"refreshToken": "secret-refresh"})
    assert response.status_code == expected
    assert "secret" not in response.text


def test_validation_does_not_echo_credentials_and_errors_are_not_cacheable(native):
    client, _, _ = native
    response = client.post("/v1/auth/native/refresh", json={"refreshToken": "secret" * 1000})
    assert response.status_code == 422
    assert "secret" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_uninvited_exchange_cannot_obtain_native_tokens(native):
    client, _, management = native
    management.authenticate_with_code.return_value.user.email = "stranger@example.com"
    response = client.post(
        "/v1/auth/native/exchange", json={"code": "code", "codeVerifier": "a" * 43}
    )
    assert response.status_code == 403
    assert "accessToken" not in response.text
