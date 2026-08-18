from fastapi.testclient import TestClient

from kenkui_server.app import create_app


def test_missing_v1_route_uses_normalized_error_with_request_id() -> None:
    response = TestClient(create_app()).get("/v1/missing", headers={"X-Request-ID": "missing-123"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "missing-123"
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not found",
            "requestId": "missing-123",
            "details": {},
        }
    }


def test_validation_failures_use_normalized_error_with_details() -> None:
    app = create_app()

    @app.get("/v1/test-validated/{count}")
    async def test_validated(count: int) -> dict[str, int]:
        return {"count": count}

    response = TestClient(app).get("/v1/test-validated/not-a-number", headers={"X-Request-ID": "valid-123"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "Request validation failed"
    assert response.json()["error"]["requestId"] == "valid-123"
    assert response.json()["error"]["details"]["errors"][0]["loc"] == ["path", "count"]


def test_unhandled_failures_are_normalized_and_receive_generated_request_ids() -> None:
    app = create_app()

    @app.get("/v1/test-failure")
    async def test_failure() -> None:
        raise RuntimeError("unexpected")

    response = TestClient(app, raise_server_exceptions=False).get("/v1/test-failure")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_server_error",
        "message": "Internal server error",
        "requestId": response.headers["X-Request-ID"],
        "details": {},
    }
    assert response.headers["X-Request-ID"]
