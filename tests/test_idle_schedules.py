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


@pytest.mark.parametrize("idle", [False, True])
def test_deployment_keeps_two_schedules_and_uses_backup_image_only_for_daily(monkeypatch, idle):
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
            Image=Image,
            Period=lambda **kwargs: kwargs,
            Volume=SimpleNamespace(from_name=lambda *args, **kwargs: object()),
            Secret=SimpleNamespace(from_name=lambda *args, **kwargs: object()),
        ),
    )
    monkeypatch.setenv("KENKUI_DEPLOYMENT", "staging")
    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1" if idle else "0")
    monkeypatch.delenv("KENKUI_MODAL_PROXY", raising=False)
    runpy.run_path(str(Path(__file__).parents[1] / "deploy/modal_app.py"))
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
