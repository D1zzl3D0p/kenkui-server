"""Exercise deployment registration without contacting Modal or building images."""

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class Image:
    def __init__(self, base):
        self.base = base
        self.packages = []
        self.environment = {}

    def env(self, values):
        self.environment.update(values)
        return self

    @classmethod
    def debian_slim(cls, **kwargs):
        return cls("debian")

    @classmethod
    def from_registry(cls, registry, **kwargs):
        assert kwargs == {"add_python": "3.12"}
        return cls(registry)

    def apt_install(self, *packages):
        self.packages.extend(packages)
        return self

    def __getattr__(self, name):
        return lambda *args, **kwargs: self


def register_deployment(monkeypatch, idle, deployment="staging", schedules=None):
    class Registration(dict):
        app_name: str

    registered = Registration()

    class App:
        def __init__(self, name):
            registered.app_name = name

        def function(self, **kwargs):
            def register(function):
                registered[function.__name__] = (function, kwargs)
                return function

            return register

    monkeypatch.setitem(
        sys.modules,
        "modal",
        SimpleNamespace(
            App=App,
            asgi_app=lambda **options: lambda function: function,
            Image=Image,
            Period=lambda **kwargs: kwargs,
            Volume=SimpleNamespace(from_name=lambda name, **kwargs: (name, kwargs)),
            Secret=SimpleNamespace(
                from_name=lambda name, *, environment_name: (name, environment_name),
                from_dict=lambda values: dict(values),
            ),
        ),
    )
    monkeypatch.setenv("KENKUI_DEPLOYMENT", deployment)
    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1" if idle else "0")
    if schedules is None:
        monkeypatch.delenv("KENKUI_BACKGROUND_SCHEDULES", raising=False)
    else:
        monkeypatch.setenv("KENKUI_BACKGROUND_SCHEDULES", schedules)
    monkeypatch.delenv("KENKUI_MODAL_PROXY", raising=False)
    runpy.run_path(str(Path(__file__).parents[1] / "deploy/modal_app.py"))
    return registered


@pytest.mark.parametrize("idle", [False, True])
def test_deployment_keeps_two_schedules_and_uses_backup_image_only_for_daily(monkeypatch, idle):
    registered = register_deployment(monkeypatch, idle, schedules="1")
    schedules = {
        name: options for name, (_, options) in registered.items() if "schedule" in options
    }
    assert len(schedules) == 2
    assert schedules["recover_jobs"]["schedule"] == {"minutes": 1}
    assert schedules["recover_jobs"]["timeout"] == 240
    assert schedules["recover_jobs"]["max_containers"] == 1
    assert schedules["retain_objects"]["schedule"] == {"hours": 24 if idle else 1}
    assert schedules["retain_objects"]["image"].base == (
        "postgres:17-bookworm" if idle else "debian"
    )
    assert registered["render_job"][1]["image"].base == "debian"
    assert ("age" in schedules["retain_objects"]["image"].packages) is idle
    for name in ("recover_jobs", "render_job", "api_bridge", "provision"):
        assert "age" not in registered[name][1]["image"].packages
    from kenkui_server import hosted

    called = []
    monkeypatch.setattr(hosted, "recover_jobs", lambda: called.append("recover"))
    monkeypatch.setattr(hosted, "retain_objects", lambda: called.append("maintenance"))
    registered["recover_jobs"][0]()
    registered["retain_objects"][0]()
    assert called == ["recover", "maintenance"]


@pytest.mark.parametrize("deployment", ["staging", "production"])
@pytest.mark.parametrize("idle", [False, True])
@pytest.mark.parametrize("schedules", [None, "0"])
def test_prelaunch_default_has_no_background_schedule_and_keeps_manual_calls(
    monkeypatch, deployment, idle, schedules
):
    registered = register_deployment(monkeypatch, idle, deployment, schedules)
    assert all(options.get("schedule") is None for _, options in registered.values())
    from kenkui_server import hosted

    called = []
    monkeypatch.setattr(hosted, "recover_jobs", lambda: called.append("recover"))
    monkeypatch.setattr(hosted, "retain_objects", lambda: called.append("maintenance"))
    registered["recover_jobs"][0]()
    registered["retain_objects"][0]()
    assert called == ["recover", "maintenance"]
    assert registered["render_job"][1]["cpu"] == 8
    assert registered["render_job"][1]["retries"] == 0
    assert registered["api_bridge"][1]["max_containers"] == 2


@pytest.mark.parametrize("value", ["true", "false", "", "2"])
def test_background_schedule_choice_rejects_invalid_values(monkeypatch, value):
    with pytest.raises(ValueError, match="KENKUI_BACKGROUND_SCHEDULES"):
        register_deployment(monkeypatch, True, schedules=value)


@pytest.mark.parametrize("suffix", ["", ".staging"])
def test_public_links_override_existing_maps_without_changing_internal_environment(
    monkeypatch, suffix
):
    web = f"https://app{suffix}.kenkui.fm"
    api = f"https://api{suffix}.kenkui.fm"
    monkeypatch.setenv("KENKUI_PUBLIC_WEB_ORIGIN", web)
    monkeypatch.setenv("KENKUI_PUBLIC_API_ORIGIN", api)
    registered = register_deployment(monkeypatch, True)
    for _, options in registered.values():
        overlays = [entry for entry in options["secrets"] if isinstance(entry, dict)]
        assert overlays == [{"KENKUI_WEB_ORIGIN": web, "KENKUI_API_ORIGIN": api}]
        worker_index = options["secrets"].index(("kenkui-staging-worker", "staging"))
        mail_index = options["secrets"].index(("kenkui-staging-mail", "staging"))
        overlay_index = options["secrets"].index(overlays[0])
        assert worker_index < mail_index < overlay_index


@pytest.mark.parametrize(
    "web,api",
    [
        ("https://app.kenkui.fm", ""),
        ("", "https://api.kenkui.fm"),
        ("https://app.kenkui.fm", "https://api.staging.kenkui.fm"),
        ("http://app.kenkui.fm", "https://api.kenkui.fm"),
        ("https://app.kenkui.fm/", "https://api.kenkui.fm"),
        ("https://evil.test", "https://api.kenkui.fm"),
    ],
)
def test_public_links_reject_partial_mixed_or_noncanonical_origins(monkeypatch, web, api):
    monkeypatch.setenv("KENKUI_PUBLIC_WEB_ORIGIN", web)
    monkeypatch.setenv("KENKUI_PUBLIC_API_ORIGIN", api)
    with pytest.raises(ValueError, match="public origins"):
        register_deployment(monkeypatch, True)


@pytest.mark.parametrize(
    ("deployment", "idle", "expected_secrets"),
    [
        (
            "staging",
            True,
            [
                ("kenkui-staging-worker", "staging"),
                ("kenkui-staging-mail", "staging"),
                ("kenkui-staging-database-idle", "staging"),
            ],
        ),
        (
            "production",
            True,
            [
                ("kenkui-production-worker", "production"),
                ("kenkui-production-mail", "production"),
                ("kenkui-production-database-idle", "production"),
            ],
        ),
        (
            "staging",
            False,
            [("kenkui-staging-worker", "staging"), ("kenkui-staging-mail", "staging")],
        ),
        (
            "production",
            False,
            [("kenkui-production-worker", "production"), ("kenkui-production-mail", "production")],
        ),
    ],
)
def test_deployment_secrets_preserve_existing_secrets_with_idle_database_overlay_last(
    monkeypatch, deployment, idle, expected_secrets
):
    registered = register_deployment(monkeypatch, idle, deployment)

    assert set(registered) == {
        "render_job",
        "api_bridge",
        "provision",
        "recover_jobs",
        "retain_objects",
    }
    for name, (_, options) in registered.items():
        bridge_secret = (
            [(f"kenkui-{deployment}-bridge", deployment)] if name == "api_bridge" else []
        )
        backup_secret = (
            [(f"kenkui-{deployment}-database-backup", deployment)]
            if idle and name == "retain_objects"
            else []
        )
        assert options["secrets"] == bridge_secret + expected_secrets + backup_secret
