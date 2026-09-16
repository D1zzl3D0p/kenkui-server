from pathlib import Path

import kenkui as kk
from fastapi.testclient import TestClient

from kenkui_server.app import create_app


def test_local_assets_and_voices_do_not_expose_private_paths(tmp_path: Path) -> None:
    voice = kk.Voice("narrator", "Narrator", True, "local", "test", True)
    with TestClient(create_app(data_dir=tmp_path / "state", voices=(voice,))) as client:
        upload = client.post(
            "/v1/assets",
            content=b"not a real epub",
            headers={"Content-Type": "application/epub+zip"},
        )

        assert upload.status_code == 201
        assert set(upload.json()) == {"id", "format", "sha256"}
        assert client.get("/v1/voices").json() == {
            "items": [
                {
                    "id": "narrator",
                    "name": "Narrator",
                    "language": None,
                    "licenseId": "test",
                    "voiceRights": None,
                }
            ]
        }


def test_configured_web_build_serves_assets_and_spa_fallback_without_shadowing_v1(
    tmp_path: Path,
) -> None:
    web_build = tmp_path / "web"
    assets = web_build / "assets"
    assets.mkdir(parents=True)
    (web_build / "index.html").write_text("<!doctype html><title>Kenkui</title>")
    (assets / "app.js").write_text("console.log('kenkui')")

    with TestClient(create_app(data_dir=tmp_path / "state", web_build_path=web_build)) as client:
        asset = client.get("/assets/app.js")
        fallback = client.get("/jobs/job-1")
        health = client.get("/v1/health")
        missing_api = client.get("/v1/missing")

    assert asset.status_code == 200
    assert asset.text == "console.log('kenkui')"
    assert fallback.status_code == 200
    assert fallback.text == "<!doctype html><title>Kenkui</title>"
    assert health.json() == {"status": "ok"}
    assert missing_api.status_code == 404
    assert missing_api.json()["error"]["code"] == "not_found"


def test_allows_configured_cross_origin_clients(tmp_path):
    """A browser served from a different origin must be able to call /v1."""
    with TestClient(
        create_app(data_dir=tmp_path / "state", allowed_origins=["https://app.kenkui.example"])
    ) as client:
        response = client.get("/v1/capabilities", headers={"Origin": "https://app.kenkui.example"})

    assert response.headers["access-control-allow-origin"] == "https://app.kenkui.example"


def test_omits_cors_headers_when_no_origins_are_configured(tmp_path):
    """A loopback-only server stays closed by default."""
    with TestClient(create_app(data_dir=tmp_path / "state")) as client:
        response = client.get("/v1/capabilities", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers
