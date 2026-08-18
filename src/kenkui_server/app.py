"""FastAPI application factory for the versioned local API."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import kenkui as kk

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from kenkui_server.api import assets, billing, jobs, voices as voices_api
from kenkui_server.compute.local import LocalProcessRunner
from kenkui_server.config import Capabilities, local_capabilities
from kenkui_server.errors import ErrorDetail, ErrorResponse
from kenkui_server.jobs.dispatcher import Dispatcher
from kenkui_server.observability import log_event
from kenkui_server.storage.assets import AssetStore
from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LocalServices:
    """Application-owned local dependencies shared by route modules."""

    repositories: Repositories
    assets: AssetStore
    voices: tuple[kk.Voice, ...]
    dispatcher: Dispatcher

REQUEST_ID_HEADER = "X-Request-ID"

ERROR_RESPONSES = {
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
    voices: Sequence[kk.Voice] = (),
    fixture_mode: bool = False,
    web_build_path: str | Path | None = None,
) -> FastAPI:
    """Create the local Kenkui API application and its private durable state."""
    root = Path(data_dir) if data_dir is not None else Path.home() / ".local" / "share" / "kenkui-server"
    database = Database(root / "server.sqlite3")
    app = FastAPI(
        title="Kenkui Server API",
        version="1.0.0",
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
    )
    repositories = Repositories(database)
    asset_store = AssetStore(root / "assets")
    runner = LocalProcessRunner(database.path, asset_store.root, fixture_mode=fixture_mode)
    dispatcher = Dispatcher(repositories, runner)
    app.state.local_services = LocalServices(
        repositories=repositories,
        assets=asset_store,
        voices=tuple(voices),
        dispatcher=dispatcher,
    )
    dispatcher.recover()
    app.include_router(assets.router)
    app.include_router(voices_api.router)
    app.include_router(billing.router)
    app.include_router(jobs.router)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response(
                request, status_code=404, code="not_found", message="Not found"
            )
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
            message="Request failed",
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
            details={"errors": jsonable_encoder(exc.errors())},
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

    @app.get("/v1/health", responses=ERROR_RESPONSES)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/v1/capabilities",
        response_model=Capabilities,
        responses=ERROR_RESPONSES,
    )
    async def capabilities() -> Capabilities:
        return local_capabilities()

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

    return app
