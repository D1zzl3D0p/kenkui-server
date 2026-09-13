import importlib.util
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
