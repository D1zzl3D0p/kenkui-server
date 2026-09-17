"""AuthKit browser sessions with an explicit private-beta invitation allowlist."""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from kenkui_server.auth.base import Identity
from kenkui_server.auth.workos import IdentityRepository

SESSION_COOKIE = "__Host-kenkui-session"
STATE_COOKIE = "__Host-kenkui-login-state"
VERIFIER_COOKIE = "__Host-kenkui-login-verifier"


@dataclass(frozen=True)
class BrowserSessionConfig:
    redirect_uri: str
    web_origin: str
    cookie_password: str = field(repr=False)
    # An empty allowlist means open signup; any verified email may sign in.
    invited_emails: frozenset[str] = frozenset()
    native_redirect_uri: str | None = None

    def __post_init__(self) -> None:
        try:
            decoded = base64.b64decode(self.cookie_password, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            decoded = b""
        if len(decoded) != 32:
            raise ValueError(
                "session cookie password must be a Fernet key: 32 URL-safe base64-encoded bytes"
            )
        for url in (self.redirect_uri, self.web_origin):
            if urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
                raise ValueError("hosted browser sessions require HTTPS origins")
        if self.native_redirect_uri not in (None, "http://127.0.0.1:43827/callback"):
            raise ValueError("native redirect must be http://127.0.0.1:43827/callback")


class BrowserAuthBackend:
    def __init__(
        self, client: Any, identities: IdentityRepository, config: BrowserSessionConfig
    ) -> None:
        self.client = client
        self.identities = identities
        self.config = config

    def _identity(self, user: Any) -> Identity:
        if not user.email_verified:
            raise PermissionError("invitation_required")
        invited = {email.strip().casefold() for email in self.config.invited_emails}
        if invited and user.email.casefold() not in invited:
            raise PermissionError("invitation_required")
        return Identity(self.identities.user_id_for_subject(user.id), user.id)

    def session(self, token: str) -> Any:
        return self.client.user_management.load_sealed_session(
            sealed_session=token, cookie_password=self.config.cookie_password
        )

    def authenticate_browser(self, token: str) -> tuple[Identity, str | None]:
        session = self.session(token)
        result = session.authenticate()
        if result.authenticated:
            return self._identity(result.user), None
        # WorkOS returns an enum, not a string, for authentication failures.
        if getattr(result.reason, "value", result.reason) != "invalid_jwt":
            raise PermissionError("unauthenticated")
        refreshed = session.refresh()
        if not refreshed.authenticated:
            raise PermissionError("unauthenticated")
        return self._identity(refreshed.user), refreshed.sealed_session

    def authenticate(self, token: str) -> Identity:
        # Bearer clients rotate refresh tokens explicitly. An implicit refresh
        # here would discard the rotated credential and race concurrent requests.
        result = self.session(token).authenticate()
        if not result.authenticated:
            raise PermissionError("unauthenticated")
        return self._identity(result.user)

    def authorize(self, actor_id: UUID, owner_id: UUID) -> bool:
        return actor_id == owner_id


def set_session(response: Any, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=7 * 86400,
    )


def browser_auth_router(backend: BrowserAuthBackend) -> APIRouter:
    router = APIRouter(prefix="/v1/auth", tags=["auth"])

    @router.get("/login")
    def login() -> RedirectResponse:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = backend.client.user_management.get_authorization_url(
            provider="authkit",
            redirect_uri=backend.config.redirect_uri,
            state=state,
            code_challenge=challenge,
        )
        response = RedirectResponse(url, status_code=303)
        for name, value in ((STATE_COOKIE, state), (VERIFIER_COOKIE, verifier)):
            response.set_cookie(
                name, value, httponly=True, secure=True, samesite="lax", path="/", max_age=600
            )
        return response

    @router.get("/callback")
    def callback(request: Request, code: str, state: str) -> RedirectResponse:
        expected = request.cookies.get(STATE_COOKIE, "")
        verifier = request.cookies.get(VERIFIER_COOKIE, "")
        if not expected or not verifier or not secrets.compare_digest(state, expected):
            raise HTTPException(400, "invalid_login_state")
        try:
            result = backend.client.user_management.authenticate_with_code(
                code=code,
                code_verifier=verifier,
                session={"seal_session": True, "cookie_password": backend.config.cookie_password},
            )
            backend._identity(result.user)
        except PermissionError as error:
            raise HTTPException(403, "invitation_required") from error
        except Exception as error:
            raise HTTPException(401, "login_failed") from error
        if not result.sealed_session:
            raise HTTPException(401, "login_failed")
        response = RedirectResponse(backend.config.web_origin + "/jobs", status_code=303)
        response.delete_cookie(STATE_COOKIE, secure=True, httponly=True, samesite="lax")
        response.delete_cookie(VERIFIER_COOKIE, secure=True, httponly=True, samesite="lax")
        set_session(response, result.sealed_session)
        return response

    @router.get("/session")
    def current_session(request: Request) -> dict[str, str]:
        identity = getattr(request.state, "hosted_identity", None)
        if identity is None:
            raise HTTPException(401, "unauthenticated")
        return {"userId": str(identity.user_id)}

    @router.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        token = request.cookies.get(SESSION_COOKIE)
        url = (
            backend.session(token).get_logout_url(return_to=backend.config.web_origin)
            if token
            else backend.config.web_origin
        )
        response = RedirectResponse(url, status_code=303)
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
        return response

    return router
