"""Test-app registration and real bridge composition cannot select the live app."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_idle_schedules import register_deployment


def test_test_app_uses_its_own_maps_volume_and_dispatch_target_without_maintenance(monkeypatch):
    monkeypatch.setenv("KENKUI_MODAL_APP", "kenkui-staging-test")
    registered = register_deployment(monkeypatch, True)
    assert registered.app_name == "kenkui-staging-test"
    assert set(registered) == {"render_job", "api_bridge", "provision", "recover_jobs"}
    for _, options in registered.values():
        assert options["image"].environment["KENKUI_MODAL_APP"] == "kenkui-staging-test"
        assert options["image"].environment["MODAL_ENVIRONMENT"] == "staging"
        if "volumes" in options:
            assert options["volumes"]["/models"][0] == "kenkui-staging-test-models"
        names = [entry[0] for entry in options["secrets"] if isinstance(entry, tuple)]
        assert "kenkui-staging-test-worker" in names
        assert "kenkui-staging-test-database-idle" in names
        assert all(name.startswith("kenkui-staging-test-") for name in names)
        assert not any(
            name.endswith("-mail") or name.endswith("-database-backup") for name in names
        )
        assert {"KENKUI_SMTP_PASSWORD": ""} in options["secrets"]
        assert options.get("schedule") is None
    assert registered["api_bridge"][1]["secrets"][0] == ("kenkui-staging-test-bridge", "staging")


def test_test_app_mail_is_explicit_and_uses_only_its_own_map(monkeypatch):
    monkeypatch.setenv("KENKUI_MODAL_APP", "kenkui-staging-test")
    monkeypatch.setenv("KENKUI_TEST_EMAIL_NOTIFICATIONS", "1")
    registered = register_deployment(monkeypatch, True)
    for _, options in registered.values():
        names = [entry[0] for entry in options["secrets"] if isinstance(entry, tuple)]
        assert "kenkui-staging-test-mail" in names
        assert "kenkui-staging-mail" not in names


@pytest.mark.parametrize(
    "deployment,app",
    [
        ("production", "kenkui-staging-test"),
        ("staging", "kenkui-production"),
        ("staging", "another-app"),
        ("staging", ""),
    ],
)
def test_invalid_app_selection_fails_before_registration(monkeypatch, deployment, app):
    monkeypatch.setenv("KENKUI_MODAL_APP", app)
    with pytest.raises(ValueError, match="KENKUI_MODAL_APP"):
        register_deployment(monkeypatch, True, deployment)


def test_test_app_rejects_periodic_work(monkeypatch):
    monkeypatch.setenv("KENKUI_MODAL_APP", "kenkui-staging-test")
    with pytest.raises(ValueError, match="test app.*schedules"):
        register_deployment(monkeypatch, True, schedules="1")


def test_test_app_registration_accepts_mail_suppression_with_real_modal_sdk(monkeypatch):
    import modal

    monkeypatch.setenv("KENKUI_DEPLOYMENT", "staging")
    monkeypatch.setenv("KENKUI_MODAL_APP", "kenkui-staging-test")
    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1")
    monkeypatch.setenv("KENKUI_BACKGROUND_SCHEDULES", "0")
    monkeypatch.delenv("KENKUI_MODAL_PROXY", raising=False)
    deployed = runpy.run_path(str(Path(__file__).parents[1] / "deploy/modal_app.py"))
    assert isinstance(deployed["api_bridge"], modal.Function)
    assert "retain_objects" not in deployed


@pytest.mark.parametrize("app", ["kenkui-staging", "kenkui-staging-test"])
def test_actual_bridge_composition_dispatches_to_selected_app_without_database(monkeypatch, app):
    from kenkui_server import hosted, voice_catalog
    from kenkui_server.compute import api_bridge
    from kenkui_server.storage import postgres, postgres_database

    monkeypatch.setenv("KENKUI_DEPLOYMENT", "staging")
    monkeypatch.setenv("KENKUI_MODAL_APP", app)
    monkeypatch.setenv("DATABASE_URL", "synthetic-not-a-connection")
    monkeypatch.setenv("KENKUI_BRIDGE_SECRET", "synthetic-bridge-secret")
    monkeypatch.setattr(
        postgres_database, "PostgresDatabase", lambda *a, **k: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(postgres, "PostgresHostedRepository", lambda db: object())
    monkeypatch.setattr(hosted, "object_store", lambda: object())
    monkeypatch.setattr(voice_catalog, "select_hosted_voices", lambda *a: ())
    monkeypatch.setattr(api_bridge.kk, "list_voices", lambda: ())
    monkeypatch.setattr(api_bridge, "create_bridge_app", lambda **kwargs: kwargs)
    composition = api_bridge.bridge_from_environment()
    services = composition["services_factory"]()
    assert services.runner.app_name == app
    assert composition["environment"] == "staging"
