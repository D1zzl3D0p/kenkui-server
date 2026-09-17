from fastapi.testclient import TestClient


def test_local_capabilities_are_versioned(client: TestClient) -> None:
    response = client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "apiVersion": "1",
        "speechSettings": True,
        "pauseLengths": True,
        "covers": {"read": True, "upload": True, "maxUploadBytes": 8 * 1024 * 1024},
        "maxUploadBytes": 50 * 1024 * 1024,
        "auth": {"mode": "none"},
        "billing": {"mode": "unmetered"},
        "sourceFormats": ["epub"],
        "outputFormats": ["m4b"],
        "casting": {"modes": ["single"], "models": []},
    }
