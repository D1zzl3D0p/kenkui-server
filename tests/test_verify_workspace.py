"""Workspace verification fails closed and covers every selected component."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/verify-workspace.py"


def load():
    # Loading the command must never start checks or open external resources.
    spec = importlib.util.spec_from_file_location("workspace_checks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch(
        "subprocess.run",
        side_effect=AssertionError("Import started verification instead of defining the CLI"),
    ):
        spec.loader.exec_module(module)
    return module


def test_fast_checks_include_hosted_api_and_both_studio_builds():
    module = load()
    checks = module.checks("fast", "all")
    assert any(
        c.directory == "kenkui-server/hosted-api" and c.command == ("npm", "run", "typecheck")
        for c in checks
    )
    assert any(c.directory == "kenkui-server/hosted-api" and "test" in c.command for c in checks)
    assert any(c.command == ("npm", "run", "build:native") for c in checks)
    assert all("test:e2e" not in c.command for c in checks)


def test_integration_requires_explicit_disposable_database_before_any_command():
    module = load()
    for env in [{}, {"KENKUI_TEST_DATABASE_URL": "postgresql://customer@production/db"}]:
        with pytest.raises(ValueError, match="disposable"):
            module.validate_environment("integration", env)
    module.validate_environment("fast", {})
    with patch("shutil.which", return_value="/fixture/tool"):
        module.validate_environment(
            "integration",
            {"KENKUI_TEST_DATABASE_URL": "postgresql://postgres@127.0.0.1:55438/postgres"},
        )


def test_component_filter_and_command_failure_propagation(tmp_path):
    module = load()
    assert {c.directory for c in module.checks("fast", "hosted-api")} == {
        "kenkui-server/hosted-api"
    }
    failing = module.Check("kenkui-server", (sys.executable, "-c", "raise SystemExit(7)"))
    (tmp_path / "kenkui-server").mkdir()
    with pytest.raises(subprocess.CalledProcessError) as error:
        module.run_checks([failing], tmp_path)
    assert error.value.returncode == 7


def test_release_contains_integration_and_artifact_smoke():
    module = load()
    integration = module.checks("integration", "all")
    release = module.checks("release", "all")
    assert all(c in release for c in integration)
    assert any(c.command == ("npm", "run", "smoke") for c in release)
    assert any(c.command == ("npm", "run", "test:e2e") for c in integration)


def test_integration_requires_restore_clients_before_checks():
    module = load()
    with patch("shutil.which", return_value=None):
        with pytest.raises(ValueError, match="restore tools"):
            module.validate_environment(
                "integration", {"KENKUI_TEST_DATABASE_URL": module.DISPOSABLE_DATABASE}
            )
