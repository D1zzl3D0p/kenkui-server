"""Exercise deployment registration without contacting Modal or building images."""

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class Image:
    def __init__(self, base):
        self.base = base

    @classmethod
    def debian_slim(cls, **kwargs):
        return cls("debian")

    @classmethod
    def from_registry(cls, registry, **kwargs):
        assert kwargs == {"add_python": "3.12"}
        return cls(registry)

    def __getattr__(self, name):
        return lambda *args, **kwargs: self


def register_deployment(monkeypatch, idle, deployment="staging"):
    registered = {}

    class App:
        def __init__(self, name):
            pass

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
            Volume=SimpleNamespace(from_name=lambda *args, **kwargs: object()),
            Secret=SimpleNamespace(
                from_name=lambda name, *, environment_name: (name, environment_name)
            ),
        ),
    )
    monkeypatch.setenv("KENKUI_DEPLOYMENT", deployment)
    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1" if idle else "0")
    monkeypatch.delenv("KENKUI_MODAL_PROXY", raising=False)
    runpy.run_path(str(Path(__file__).parents[1] / "deploy/modal_app.py"))
    return registered


@pytest.mark.parametrize("idle", [False, True])
def test_deployment_keeps_two_schedules_and_uses_backup_image_only_for_daily(monkeypatch, idle):
    registered = register_deployment(monkeypatch, idle)
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
    from kenkui_server import hosted

    called = []
    monkeypatch.setattr(hosted, "recover_jobs", lambda: called.append("recover"))
    monkeypatch.setattr(hosted, "retain_objects", lambda: called.append("maintenance"))
    registered["recover_jobs"][0]()
    registered["retain_objects"][0]()
    assert called == ["recover", "maintenance"]


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
        assert options["secrets"] == bridge_secret + expected_secrets
