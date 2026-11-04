"""The retired WorkOS API cannot be restarted through the worker CLI."""

import os
import subprocess
import sys


def test_worker_cli_rejects_retired_serve_command_before_provider_configuration():
    environment = dict(os.environ)
    environment.pop("WORKOS_REDIRECT_URI", None)
    result = subprocess.run(
        [sys.executable, "-m", "kenkui_server.hosted", "serve"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
