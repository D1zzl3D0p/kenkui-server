"""Desktop PKCE exchange; provider secrets stay on the server.

Access credentials are sealed WorkOS sessions, held only in native memory.
The separate refresh credential is persisted by the app's OS credential store.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from kenkui_server.auth.browser import BrowserAuthBackend

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class CodeExchange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=4096)
    code_verifier: str = Field(alias="codeVerifier", pattern=r"^[A-Za-z0-9._~-]{43,128}$")


class TokenRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(alias="refreshToken", min_length=1, max_length=4096)


def provider_error(error: Exception) -> HTTPException:
    # Do not return provider exception text: it can contain credential details.
    status = getattr(getattr(error, "response", None), "status_code", None)
    return HTTPException(
        401 if status in {400, 401, 403} else 503,
        "native_auth_failed" if status in {400, 401, 403} else "auth_provider_unavailable",
        headers=NO_STORE,
    )


def token_response(backend: BrowserAuthBackend, result: Any) -> JSONResponse:
    try:
        identity = backend._identity(result.user)
    except PermissionError as error:
        raise HTTPException(403, "invitation_required", headers=NO_STORE) from error
    if not result.sealed_session or not result.refresh_token:
        raise HTTPException(503, "native_session_unavailable", headers=NO_STORE)
    try:
        # This JWT came directly from the trusted SDK exchange, not from a request.
        # Its expiry is only a refresh hint; actual authorization uses SDK validation.
        payload = result.access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expires_in = max(1, min(3600, int(claims["exp"]) - int(time.time())))
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(503, "native_session_unavailable", headers=NO_STORE) from None
    return JSONResponse(
        {
            "accessToken": result.sealed_session,
            "refreshToken": result.refresh_token,
            "expiresIn": expires_in,
            "userId": str(identity.user_id),
        },
        headers=NO_STORE,
    )


def native_auth_router(backend: BrowserAuthBackend) -> APIRouter:
    router = APIRouter(prefix="/v1/auth/native", tags=["native-auth"])

    def enabled() -> str:
        if not backend.config.native_redirect_uri:
            raise HTTPException(404, "native_auth_unavailable", headers=NO_STORE)
        return backend.config.native_redirect_uri

    @router.get("/config")
    def config() -> JSONResponse:
        return JSONResponse({"redirectUri": enabled()}, headers=NO_STORE)

    @router.get("/authorize")
    def authorize(
        state: str = Query(pattern=r"^[A-Za-z0-9_-]{43}$"),
        code_challenge: str = Query(pattern=r"^[A-Za-z0-9_-]{43}$"),
    ) -> RedirectResponse:
        redirect_uri = enabled()
        url = backend.client.user_management.get_authorization_url(
            provider="authkit",
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
        )
        return RedirectResponse(url, status_code=303, headers=NO_STORE)

    @router.post("/exchange")
    def exchange(body: CodeExchange) -> JSONResponse:
        enabled()
        try:
            result = backend.client.user_management.authenticate_with_code(
                code=body.code,
                code_verifier=body.code_verifier,
                session={"seal_session": True, "cookie_password": backend.config.cookie_password},
            )
        except Exception as error:
            raise provider_error(error) from error
        return token_response(backend, result)

    @router.post("/refresh")
    def refresh(body: TokenRefresh) -> JSONResponse:
        enabled()
        try:
            result = backend.client.user_management.authenticate_with_refresh_token(
                refresh_token=body.refresh_token,
                session={"seal_session": True, "cookie_password": backend.config.cookie_password},
            )
        except Exception as error:
            raise provider_error(error) from error
        return token_response(backend, result)

    @router.post("/logout")
    def logout(request: Request) -> Response:
        enabled()
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "unauthenticated", headers=NO_STORE)
        try:
            session = backend.session(token).authenticate()
            if not session.authenticated:
                raise HTTPException(401, "unauthenticated", headers=NO_STORE)
            backend.client.user_management.revoke_session(session_id=session.session_id)
        except HTTPException:
            raise
        except Exception as error:
            raise provider_error(error) from error
        return Response(status_code=204, headers=NO_STORE)

    return router
