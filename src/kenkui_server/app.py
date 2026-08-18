"""FastAPI application factory for the versioned local API."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from kenkui_server.config import Capabilities, local_capabilities
from kenkui_server.errors import ErrorDetail, ErrorResponse
from kenkui_server.observability import log_event

LOGGER = logging.getLogger(__name__)

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


def create_app() -> FastAPI:
    """Create the local Kenkui API application."""
    app = FastAPI(
        title="Kenkui Server API",
        version="1.0.0",
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
    )

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

    return app
