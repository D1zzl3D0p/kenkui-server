"""Real Modal dependency order must survive container-style module reimport."""

import os
import runpy
from pathlib import Path

import modal
import pytest
from modal._utils.async_utils import synchronizer

ROOT = Path(__file__).parents[1]
HAS_TEST_APP = (ROOT / "tests/test_staging_runtime_isolation.py").exists()


def dependency_signature(function):
    result = []
    # This is the same dependency API used by Modal's container hydration path.
    for wrapped in function._deps_(only_explicit_mounts=True):
        obj = synchronizer._translate_in(wrapped)
        if type(obj).__name__ == "_Secret" and obj._is_ephemeral:
            result.append(("inline-secret", tuple(sorted(obj._load_env_dict().items()))))
        else:
            result.append((type(obj).__name__, obj._name))
    return result


def register(monkeypatch):
    captured = []
    original = modal.Image.env

    def image_env(image, values):
        captured.append(dict(values))
        return original(image, values)

    with monkeypatch.context() as local:
        local.setattr(modal.Image, "env", image_env)
        module = runpy.run_path(str(ROOT / "deploy/modal_app.py"))
    dependencies = {
        name: dependency_signature(value)
        for name, value in module.items()
        if isinstance(value, modal.Function)
    }
    assert captured and all(value == captured[0] for value in captured)
    return dependencies, captured[0]


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("public_links", [False, True])
@pytest.mark.parametrize("background", ["0", "1"])
def test_default_app_dependency_order_survives_container_import(
    monkeypatch, environment, public_links, background
):
    exercise(monkeypatch, environment, public_links, background)


@pytest.mark.skipif(
    not HAS_TEST_APP, reason="Production repair does not introduce the isolated test app"
)
@pytest.mark.parametrize("public_links", [False, True])
@pytest.mark.parametrize("mail", ["0", "1"])
def test_isolated_app_dependency_order_survives_container_import(monkeypatch, public_links, mail):
    exercise(monkeypatch, "staging", public_links, "0", mail)


def exercise(monkeypatch, environment, public_links, background, test_mail=None):
    for key in list(os.environ):
        if key.startswith("KENKUI_") or key == "MODAL_ENVIRONMENT":
            monkeypatch.delenv(key)
    monkeypatch.setenv("KENKUI_DEPLOYMENT", environment)
    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1")
    monkeypatch.setenv("KENKUI_BACKGROUND_SCHEDULES", background)
    if test_mail is not None:
        monkeypatch.setenv("KENKUI_MODAL_APP", "kenkui-staging-test")
        monkeypatch.setenv("KENKUI_TEST_EMAIL_NOTIFICATIONS", test_mail)
    if public_links:
        suffix = ".staging" if environment == "staging" else ""
        monkeypatch.setenv("KENKUI_PUBLIC_WEB_ORIGIN", f"https://app{suffix}.kenkui.fm")
        monkeypatch.setenv("KENKUI_PUBLIC_API_ORIGIN", f"https://api{suffix}.kenkui.fm")
    deployed, image_environment = register(monkeypatch)
    for key in list(os.environ):
        if key.startswith("KENKUI_") or key == "MODAL_ENVIRONMENT":
            monkeypatch.delenv(key)
    for key, value in image_environment.items():
        monkeypatch.setenv(key, value)
    # Only the public, locally created inline maps contribute these runtime vars.
    for kind, detail in deployed["api_bridge"]:
        if kind == "inline-secret":
            for key, value in detail:
                monkeypatch.setenv(key, value)
    reimported, container_environment = register(monkeypatch)
    assert reimported == deployed
    assert container_environment == image_environment
