"""Verify the requirements actually selected by the Modal image builder."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from test_idle_schedules import register_deployment

ROOT = Path(__file__).parents[1]


def export_requirements(command: str, tmp_path: Path) -> set[str]:
    arguments = shlex.split(command)
    output = tmp_path / "requirements.txt"
    arguments[arguments.index("--output-file") + 1] = str(output)
    subprocess.run(
        [*arguments, "--offline", "--python", sys.executable],
        cwd=ROOT,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        line.split("==", 1)[0].lower()
        for line in output.read_text().splitlines()
        if "==" in line and not line.startswith("#")
    }


@pytest.mark.parametrize("idle", [False, True])
def test_worker_images_export_cloud_dependencies_without_workos(monkeypatch, tmp_path, idle):
    registered = register_deployment(monkeypatch, idle)
    images = {id(options["image"]): options["image"] for _, options in registered.values()}
    for image in images.values():
        command = next(command for command in image.commands if command.startswith("uv export "))
        dependencies = export_requirements(command, tmp_path)
        assert {"boto3", "modal", "psycopg", "psycopg-binary", "psycopg-pool"} <= dependencies
        assert "workos" not in dependencies
