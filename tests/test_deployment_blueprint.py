import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "render_blueprint", Path(__file__).parents[1] / "deploy/render_blueprint.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_environments_do_not_share_databases_or_modal_apps():
    image = "ghcr.io/kenkui/server@sha256:" + "a" * 64
    staging = module.blueprint("staging", image, ["192.0.2.1/32"])
    production = module.blueprint("production", image, ["192.0.2.2/32"])
    assert staging["databases"][0]["name"] != production["databases"][0]["name"]
    for document, environment in ((staging, "staging"), (production, "production")):
        service = document["services"][0]
        variables = {entry["key"]: entry for entry in service["envVars"]}
        assert variables["MODAL_ENVIRONMENT"]["value"] == environment
        assert variables["DATABASE_URL"]["fromDatabase"]["name"] == document["databases"][0]["name"]
        assert variables["KENKUI_INVITED_EMAILS"]["sync"] is False
        assert service["image"]["url"] == image


def test_rejects_mutable_images():
    with pytest.raises(ValueError):
        module.blueprint("production", "ghcr.io/kenkui/server:latest")


def test_dynamic_modal_network_does_not_require_a_proxy():
    document = module.blueprint("production", "ghcr.io/kenkui/server@sha256:" + "a" * 64)
    assert document["databases"][0]["ipAllowList"][0]["source"] == "0.0.0.0/0"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_idle_hosting_creates_only_a_free_replacement_api(environment):
    image = "ghcr.io/kenkui/server@sha256:" + "a" * 64
    document = module.blueprint(environment, image, idle_hosting=True)
    assert "databases" not in document
    assert "fromDatabase" not in json.dumps(document)
    assert len(document["services"]) == 1
    service = document["services"][0]
    assert service["name"] == f"kenkui-{environment}-api-idle"
    assert service["plan"] == "free"
    assert service["image"]["url"] == image
    assert "preDeployCommand" not in service
    variables = {entry["key"]: entry for entry in service["envVars"]}
    assert variables["DATABASE_URL"] == {"key": "DATABASE_URL", "sync": False}
    assert variables["KENKUI_DATABASE_IDLE"]["value"] == "1"
    assert variables["MODAL_ENVIRONMENT"]["value"] == environment
    assert variables["KENKUI_MODAL_APP"]["value"] == f"kenkui-{environment}"
    suffix = ".staging" if environment == "staging" else ""
    assert service["domains"] == [f"api{suffix}.kenkui.fm"]
    assert variables["WORKOS_REDIRECT_URI"]["value"] == (
        f"https://api{suffix}.kenkui.fm/v1/auth/callback"
    )
    assert variables["KENKUI_WEB_ORIGIN"]["value"] == f"https://app{suffix}.kenkui.fm"


def test_idle_hosting_rejects_misleading_worker_database_allowlist():
    with pytest.raises(ValueError, match="worker CIDRs.*external"):
        module.blueprint(
            "staging",
            "ghcr.io/kenkui/server@sha256:" + "a" * 64,
            ["192.0.2.1/32"],
            idle_hosting=True,
        )


def test_idle_hosting_cli():
    result = subprocess.run(
        [
            sys.executable,
            str(Path(module.__file__)),
            "--environment",
            "staging",
            "--image",
            "ghcr.io/kenkui/server@sha256:" + "a" * 64,
            "--idle-hosting",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["services"][0]["plan"] == "free"


def test_legacy_hosting_still_provisions_paid_service_and_database():
    document = module.blueprint("production", "ghcr.io/kenkui/server@sha256:" + "a" * 64)
    service = document["services"][0]
    assert service["name"] == "kenkui-production-api"
    assert service["plan"] == "1c-2g"
    assert service["preDeployCommand"] == "python -m kenkui_server.hosted migrate"
    assert document["databases"][0]["name"] == "kenkui-production-db"
    assert all(entry["key"] != "KENKUI_DATABASE_IDLE" for entry in service["envVars"])
