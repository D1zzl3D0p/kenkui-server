from fastapi.testclient import TestClient


def test_health_reports_ok_and_propagates_request_id(client: TestClient) -> None:
    response = client.get("/v1/health", headers={"X-Request-ID": "health-123"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "health-123"


def test_health_generates_request_id_when_header_is_empty(client: TestClient) -> None:
    response = client.get("/v1/health", headers={"X-Request-ID": ""})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
