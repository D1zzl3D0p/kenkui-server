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


@pytest.mark.parametrize(
    "image,cidrs",
    [
        ("ghcr.io/kenkui/server:latest", ["192.0.2.1/32"]),
        ("ghcr.io/kenkui/server@sha256:" + "a" * 64, ["0.0.0.0/0"]),
        ("ghcr.io/kenkui/server@sha256:" + "a" * 64, []),
    ],
)
def test_rejects_mutable_images_and_unrestricted_database_ingress(image, cidrs):
    with pytest.raises(ValueError):
        module.blueprint("production", image, cidrs)
