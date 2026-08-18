import json
import logging

from fastapi.testclient import TestClient

from kenkui_server.app import create_app


def test_request_completion_is_logged_as_structured_event(caplog) -> None:
    caplog.set_level(logging.INFO, logger="kenkui_server.app")

    response = TestClient(create_app()).get("/v1/health", headers={"X-Request-ID": "log-123"})

    assert response.status_code == 200
    event = json.loads(caplog.records[-1].getMessage())
    assert event == {
        "event": "request.completed",
        "method": "GET",
        "path": "/v1/health",
        "requestId": "log-123",
        "statusCode": 200,
    }


def test_unhandled_failure_is_logged_as_structured_event(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="kenkui_server.app")
    app = create_app()

    @app.get("/v1/test-failure")
    async def test_failure() -> None:
        raise RuntimeError("unexpected")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/v1/test-failure", headers={"X-Request-ID": "failure-123"}
    )

    assert response.status_code == 500
    event = json.loads(caplog.records[-1].getMessage())
    assert event == {
        "code": "internal_server_error",
        "event": "request.failed",
        "method": "GET",
        "path": "/v1/test-failure",
        "requestId": "failure-123",
        "statusCode": 500,
    }
