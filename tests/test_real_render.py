"""Opt-in acceptance through the actual API and nested synthesis process."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kenkui_server.app import create_app


@pytest.mark.skipif(
    not os.environ.get("KENKUI_BETA_TEST_VOICE"), reason="requires a provisioned voice"
)
def test_server_produces_playable_m4b(tmp_path: Path) -> None:
    web_root = Path(
        os.environ.get("KENKUI_WEB_ROOT") or Path(__file__).resolve().parents[2] / "kenkui-studio"
    )
    source = web_root / "tests/fixtures/book.epub"
    with TestClient(create_app(data_dir=tmp_path / "state")) as client:
        asset = client.post(
            "/v1/assets",
            content=source.read_bytes(),
            headers={"Content-Type": "application/epub+zip"},
        )
        assert asset.status_code == 201
        book = client.get(f"/v1/assets/{asset.json()['id']}/book").json()
        payload = {
            "sourceId": asset.json()["id"],
            "chapters": [book["chapters"][0]["id"]],
            "casting": {"voiceId": os.environ["KENKUI_BETA_TEST_VOICE"]},
            "output": {"title": "Beta acceptance", "author": "Kenkui", "sourceCover": False},
        }
        assert client.post("/v1/jobs/preflight", json=payload).status_code == 200
        response = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "real-render"})
        assert response.status_code == 202
        job_id = response.json()["id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = client.get(f"/v1/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        assert job["status"] == "succeeded", job
        artifact = client.get(f"/v1/jobs/{job_id}/artifact")
        assert artifact.status_code == 200
        output = tmp_path / "acceptance.m4b"
        output.write_bytes(artifact.content)
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_chapters",
                "-of",
                "json",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        metadata = json.loads(probe.stdout)
        assert float(metadata["format"]["duration"]) > 0
        assert metadata["format"]["tags"]["title"] == "Beta acceptance"
        assert len(metadata["chapters"]) == 1
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"],
            check=True,
            capture_output=True,
        )
