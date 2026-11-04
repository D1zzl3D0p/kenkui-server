"""The locale verifier must retain assertions before it can inspect Docker."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[1] / "scripts/neon/test/paired-locale.py"


@pytest.mark.parametrize(
    ("flags", "optimization"),
    [(["-O"], None), (["-OO"], None), ([], "1"), ([], "2"), ([], None)],
)
def test_locale_driver_refuses_optimization_before_docker(tmp_path, flags, optimization):
    """Removing the guard must fail without contacting any actual Docker service."""
    marker = tmp_path / "docker-calls.jsonl"
    spy = tmp_path / "docker"
    spy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_SPY_MARKER'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.stderr.write('safe_docker_spy: refusing all Docker operations\\n')\n"
        "sys.exit(97)\n"
    )
    spy.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "DOCKER_SPY_MARKER": str(marker),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("PYTHONOPTIMIZE", None)
    if optimization is not None:
        env["PYTHONOPTIMIZE"] = optimization
    result = subprocess.run(
        [sys.executable, *flags, str(DRIVER)], env=env, capture_output=True, text=True, timeout=15
    )
    assert result.returncode != 0
    if flags or optimization:
        assert not marker.exists(), result.stderr
        assert "This rehearsal requires Python assertions" in result.stderr
    else:
        # Normal Python passes the guard and stops at the spy's first exact-name inspect.
        assert [json.loads(line) for line in marker.read_text().splitlines()] == [
            ["container", "inspect", "kenkui-neon-locale-source-20261103"]
        ]
        assert "safe_docker_spy" in result.stderr
