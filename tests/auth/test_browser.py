from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from kenkui_server.app import create_app
from kenkui_server.auth.browser import BrowserAuthBackend, BrowserSessionConfig, SESSION_COOKIE
from kenkui_server.auth.workos import InMemoryIdentityRepository


def backend():
    user = SimpleNamespace(id="user", email="invited@example.com", email_verified=True)
    management = Mock()
    management.get_authorization_url.return_value = "https://auth.example.com/login"
    management.authenticate_with_code.return_value = SimpleNamespace(
        user=user, sealed_session="sealed"
    )
    management.load_sealed_session.return_value.authenticate.return_value = SimpleNamespace(
        authenticated=True, user=user
    )
    config = BrowserSessionConfig(
        "https://api.example.com/v1/auth/callback",
        "https://app.example.com",
        "x" * 32,
        frozenset({"invited@example.com"}),
    )
    return BrowserAuthBackend(
        SimpleNamespace(user_management=management), InMemoryIdentityRepository(), config
    )


def test_login_checks_state_and_sets_host_only_secure_session(tmp_path):
    auth = backend()
    app = create_app(
        data_dir=tmp_path, voices=(), auth_backend=auth, job_owner_resolver=lambda _: UUID(int=1)
    )
    with TestClient(app, base_url="https://api.example.com") as client:
        login = client.get("/v1/auth/login", follow_redirects=False)
        assert login.status_code == 303
        call = auth.client.user_management.get_authorization_url.call_args.kwargs
        assert call["code_challenge"]
        invalid = client.get("/v1/auth/callback", params={"code": "code", "state": "wrong"})
        assert invalid.status_code == 400
        auth.client.user_management.authenticate_with_code.assert_not_called()
        response = client.get(
            "/v1/auth/callback",
            params={"code": "code", "state": call["state"]},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert client.cookies[SESSION_COOKIE] == "sealed"
        cookies = response.headers.get_list("set-cookie")
        assert any(
            SESSION_COOKIE in value
            and "Secure" in value
            and "HttpOnly" in value
            and "SameSite=lax" in value
            for value in cookies
        )
        assert client.get("/v1/auth/session").status_code == 200
        assert (
            client.post(
                "/v1/jobs", headers={"Origin": "https://stranger.example.com"}, json={}
            ).status_code
            == 403
        )


def test_uninvited_and_unverified_users_cannot_use_sessions():
    auth = backend()
    for user in [
        SimpleNamespace(id="user", email="stranger@example.com", email_verified=True),
        SimpleNamespace(id="user", email="invited@example.com", email_verified=False),
    ]:
        auth.client.user_management.load_sealed_session.return_value.authenticate.return_value = (
            SimpleNamespace(authenticated=True, user=user)
        )
        with pytest.raises(PermissionError):
            auth.authenticate("sealed")
