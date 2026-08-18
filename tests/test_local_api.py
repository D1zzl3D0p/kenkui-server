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
            "items": [{"id": "narrator", "name": "Narrator", "language": None}]
        }
