"""FastAPI application factory for the versioned local API."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import anyio
import kenkui as kk
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from kenkui_server.api import assets, billing, jobs
from kenkui_server.api import voices as voices_api
from kenkui_server.api.schemas import EventResponse
from kenkui_server.auth.base import AuthBackend, Identity
from kenkui_server.auth.browser import (
    SESSION_COOKIE,
    BrowserAuthBackend,
    browser_auth_router,
    set_session,
)
from kenkui_server.auth.native import native_auth_router
from kenkui_server.billing.service import BillingService
from kenkui_server.billing.stripe import StripeWebhookHandler
from kenkui_server.compute.base import ProcessRunner
from kenkui_server.compute.local import LocalProcessRunner
from kenkui_server.config import (
    AuthCapabilities,
    BillingCapabilities,
    Capabilities,
    HostedConfig,
    local_capabilities,
)
from kenkui_server.errors import ErrorDetail, ErrorResponse
from kenkui_server.jobs.dispatcher import Dispatcher, HostedDispatcher
from kenkui_server.observability import log_event
from kenkui_server.storage.assets import AssetStore
from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LocalServices:
    """Application-owned dependencies shared by route modules."""

    repositories: Any
    assets: Any
    voices: tuple[kk.Voice, ...]
    dispatcher: Any


@dataclass(frozen=True, slots=True)
class HostedServices:
    """Provider-neutral hosted dependencies selected by deployment configuration."""

    repositories: Any
    assets: Any
    voices: tuple[kk.Voice, ...]
    runner: ProcessRunner
    auth_backend: AuthBackend
    account_id_for_identity: Callable[[UUID], str]


@dataclass(frozen=True, slots=True)
class HostedAuthServices:
    """Hosted authentication and ownership lookup used only when configured."""

    backend: AuthBackend
    job_owner_resolver: Callable[[str], UUID]
    asset_owner_resolver: Callable[[str], UUID] | None = None


def hosted_identity(request: Request) -> Identity | None:
    """Return the identity established by hosted authentication middleware."""
    return getattr(request.state, "hosted_identity", None)


REQUEST_ID_HEADER = "X-Request-ID"

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    500: {
        "model": ErrorResponse,
        "description": "Normalized internal server error",
    }
}


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = str(uuid4())
        request.state.request_id = request_id
    return request_id


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=_request_id(request),
            details=details or {},
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(by_alias=True),
        headers={REQUEST_ID_HEADER: _request_id(request)},
    )


def create_app(
    *,
    data_dir: str | Path | None = None,
    voices: Sequence[kk.Voice] | None = None,
    model_allowlist: tuple[str, ...] = (),
    max_upload_bytes: int = 50 * 1024 * 1024,
    max_speech_characters: int = 10_000_000,
    fixture_mode: bool = False,
    max_jobs: int = 2,
    render_workers: int = 1,
    web_build_path: str | Path | None = None,
    allowed_origins: list[str] | None = None,
    auth_backend: AuthBackend | None = None,
    job_owner_resolver: Callable[[str], UUID] | None = None,
    asset_owner_resolver: Callable[[str], UUID] | None = None,
    hosted_config: HostedConfig | None = None,
    hosted_services: HostedServices | None = None,
) -> FastAPI:
    """Create local services by default or hosted services when configured."""
    if (hosted_config is None) != (hosted_services is None):
        raise ValueError("hosted configuration requires hosted services")
    if hosted_config is not None and (
        auth_backend is not None
        or job_owner_resolver is not None
        or asset_owner_resolver is not None
    ):
        raise ValueError("hosted configuration owns authentication")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        local_runner = getattr(application.state, "local_runner", None)
        if local_runner is not None:
            local_runner.close()
        hosted_database = getattr(application.state, "hosted_database", None)
        if hosted_database is not None:
            hosted_database.close()

    app = FastAPI(
        lifespan=lifespan,
        title="Kenkui Server API",
        version="1.0.0",
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
    )
    if hosted_services is None:
        root = (
            Path(data_dir)
            if data_dir is not None
            else Path.home() / ".local" / "share" / "kenkui-server"
        )
        database = Database(root / "server.sqlite3")
        repositories = Repositories(database)
        asset_store = AssetStore(root / "assets")
        runner = LocalProcessRunner(
            database.path,
            asset_store.root,
            fixture_mode=fixture_mode,
            max_jobs=max_jobs,
            render_workers=render_workers,
        )
        app.state.local_runner = runner
        dispatcher: Any = Dispatcher(repositories, runner)
        services = LocalServices(
            repositories=repositories,
            assets=asset_store,
            voices=tuple(voices)
            if voices is not None
            else tuple(
                voice for voice in kk.list_voices() if voice.enabled and voice.state == "loaded"
            ),
            dispatcher=dispatcher,
        )
        app.state.local_services = services
        if (auth_backend is None) != (job_owner_resolver is None):
            raise ValueError("hosted authentication requires an owner resolver")
        app.state.hosted_auth = (
            HostedAuthServices(auth_backend, job_owner_resolver, asset_owner_resolver)
            if auth_backend is not None and job_owner_resolver is not None
            else None
        )
        app.state.hosted_services = None
    else:
        dispatcher = HostedDispatcher(hosted_services.repositories, hosted_services.runner)
        services = LocalServices(
            repositories=hosted_services.repositories,
            assets=hosted_services.assets,
            voices=hosted_services.voices,
            dispatcher=dispatcher,
        )
        app.state.hosted_auth = HostedAuthServices(
            hosted_services.auth_backend,
            hosted_services.repositories.jobs.owner_id,
            hosted_services.repositories.assets.owner_id,
        )
        app.state.hosted_services = hosted_services
    if max_upload_bytes < 1:
        raise ValueError("max_upload_bytes must be positive")
    if max_speech_characters < 1:
        raise ValueError("max_speech_characters must be positive")
    app.state.max_speech_characters = max_speech_characters
    app.state.max_upload_bytes = max_upload_bytes
    app.state.model_allowlist = model_allowlist
    app.state.stripe_checkout = None
    if hosted_config is not None and hosted_config.stripe_secret_key.get_secret_value():
        from kenkui_server.billing.stripe import StripeCheckout

        if not hosted_config.stripe_webhook_secret.get_secret_value():
            raise ValueError("Stripe checkout requires a webhook signing secret")
        app.state.stripe_checkout = StripeCheckout(
            secret_key=hosted_config.stripe_secret_key.get_secret_value(),
            web_origin=hosted_config.web_origin,
        )
    app.state.services = services
    dispatcher.recover()
    app.include_router(assets.router)
    app.include_router(voices_api.router)
    app.include_router(billing.router)
    if (
        hosted_config is not None
        and hosted_services is not None
        and hosted_config.stripe_webhook_secret.get_secret_value()
    ):
        handler = StripeWebhookHandler(
            BillingService(hosted_services.repositories.billing),
            signing_secret=hosted_config.stripe_webhook_secret.get_secret_value(),
        )
        app.include_router(billing.stripe_webhook_router(handler))
    app.include_router(jobs.router)
    configured_auth = app.state.hosted_auth
    if configured_auth is not None and isinstance(configured_auth.backend, BrowserAuthBackend):
        app.include_router(browser_auth_router(configured_auth.backend))
        app.include_router(native_auth_router(configured_auth.backend))
    elif configured_auth is not None:

        @app.get("/v1/auth/session")
        def current_bearer_identity(request: Request) -> dict[str, str]:
            identity = hosted_identity(request)
            if identity is None:
                raise HTTPException(401, "unauthenticated")
            return {"userId": str(identity.user_id)}

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response(request, status_code=404, code="not_found", message="Not found")
        if exc.status_code == 405:
            return _error_response(
                request,
                status_code=405,
                code="method_not_allowed",
                message="Method not allowed",
            )
        return _error_response(
            request,
            status_code=exc.status_code,
            code="http_error",
            message=exc.detail if isinstance(exc.detail, str) else "Request failed",
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="validation_error",
            message="Request validation failed",
            # Native auth bodies contain refresh credentials and PKCE material.
            details=None
            if request.url.path.startswith("/v1/auth/native/")
            else {"errors": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        log_event(
            LOGGER,
            "request.failed",
            level=logging.ERROR,
            code="internal_server_error",
            method=request.method,
            path=request.url.path,
            requestId=_request_id(request),
            statusCode=500,
        )
        return _error_response(
            request,
            status_code=500,
            code="internal_server_error",
            message="Internal server error",
        )

    @app.middleware("http")
    async def propagate_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER)
        request.state.request_id = request_id if request_id else str(uuid4())
        response = await call_next(request)
        if request.url.path.startswith("/v1/auth/native/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        log_event(
            LOGGER,
            "request.completed",
            method=request.method,
            path=request.url.path,
            requestId=_request_id(request),
            statusCode=response.status_code,
        )
        response.headers[REQUEST_ID_HEADER] = _request_id(request)
        return response

    @app.middleware("http")
    async def authenticate_hosted_protected_routes(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        hosted_auth: HostedAuthServices | None = request.app.state.hosted_auth
        path = request.url.path
        protected = (
            path.startswith("/v1/jobs")
            or path.startswith("/v1/assets")
            or (path.startswith("/v1/billing") and path != "/v1/billing/webhooks/stripe")
            or path in {"/v1/auth/session", "/v1/auth/logout"}
        )
        if hosted_auth is None or not protected:
            return await call_next(request)
        scheme, _, bearer = request.headers.get("Authorization", "").partition(" ")
        cookie = request.cookies.get(SESSION_COOKIE)
        token = bearer if scheme.lower() == "bearer" else cookie
        if not token:
            return _error_response(
                request, status_code=401, code="unauthenticated", message="Sign in to continue."
            )
        refreshed = None
        try:
            backend = hosted_auth.backend
            if cookie and scheme.lower() != "bearer" and isinstance(backend, BrowserAuthBackend):
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    redirect = urlsplit(backend.config.redirect_uri)
                    trusted = {backend.config.web_origin, f"{redirect.scheme}://{redirect.netloc}"}
                    if request.headers.get("Origin") not in trusted:
                        return _error_response(
                            request,
                            status_code=403,
                            code="invalid_origin",
                            message="Request origin is not allowed.",
                        )
                identity, refreshed = await anyio.to_thread.run_sync(
                    backend.authenticate_browser, token
                )
            else:
                identity = await anyio.to_thread.run_sync(backend.authenticate, token)
            request.state.hosted_identity = identity
        except PermissionError:
            return _error_response(
                request,
                status_code=401,
                code="unauthenticated",
                message="Sign in with an invited account to continue.",
            )
        response = await call_next(request)
        if refreshed is not None:
            set_session(response, refreshed)
        return response

    @app.get("/v1/health", responses=ERROR_RESPONSES)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/v1/capabilities",
        response_model=Capabilities,
        responses=ERROR_RESPONSES,
    )
    async def capabilities() -> Capabilities:
        result = local_capabilities(model_allowlist)
        result.max_upload_bytes = max_upload_bytes
        auth = app.state.hosted_auth
        if auth is not None:
            result.auth = AuthCapabilities(
                mode="session" if isinstance(auth.backend, BrowserAuthBackend) else "bearer"
            )
        if hosted_services is not None:
            result.billing = BillingCapabilities(mode="credits")
        return result

    def openapi_with_events() -> dict[str, Any]:
        if app.openapi_schema is None:
            document = get_openapi(title=app.title, version=app.version, routes=app.routes)
            event = EventResponse.model_json_schema(
                by_alias=True, ref_template="#/components/schemas/{model}"
            )
            definitions = event.pop("$defs", {})
            document["components"]["schemas"].update(definitions)
            document["components"]["schemas"]["EventResponse"] = event
            app.openapi_schema = document
        return app.openapi_schema

    app.openapi = openapi_with_events  # type: ignore[method-assign]

    if web_build_path is not None:
        web_build = Path(web_build_path).resolve()
        index = web_build / "index.html"
        if not index.is_file():
            raise ValueError("web_build_path must contain index.html")

        @app.get("/{path:path}", include_in_schema=False)
        async def serve_web(path: str) -> Response:
            """Serve installed SPA assets and fall back to its entry point."""
            if path == "v1" or path.startswith("v1/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = (web_build / path).resolve()
            if candidate.is_relative_to(web_build) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    # CORS must wrap authentication, including its early 401/403 responses and
    # unauthenticated browser preflight requests.
    if allowed_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Idempotency-Key", "Authorization"],
        )
    return app
