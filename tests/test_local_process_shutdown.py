"""Desktop shutdown policy remains opt-in for standalone local servers."""

from unittest.mock import Mock

import pytest

from kenkui_server.compute.local import LocalProcessRunner
from kenkui_server.jobs.models import (
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.jobs.transitions import DispatchRequested, transition
from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("running", [False, True])
def test_shutdown_policy_controls_lifetime_and_cancellation(
    tmp_path, monkeypatch, managed, running
):
    path = tmp_path / "server.sqlite3"
    database = Database(path)
    repositories = Repositories(database)
    spec = JobSpec(
        "source", ("chapter",), SingleVoiceCasting("voice"), TtsSettings(), OutputSpec("out.m4b")
    )
    repositories.create_job_and_dispatch(
        Job("job", spec), Dispatch("dispatch", "job", "pending"), idempotency_key=None
    )
    if running:
        repositories.update_job_and_append_event(
            transition(repositories.jobs.get("job"), DispatchRequested()),
            expected_version=0,
            event_type="running",
        )
    runner = LocalProcessRunner(path, tmp_path / "assets", stop_workers_on_close=managed)
    child = Mock()
    child.poll.return_value = 0
    child.wait.return_value = 0
    calls = []

    def spawn(command, **kwargs):
        calls.append((command, kwargs))
        runner._stop.set()
        return child

    monkeypatch.setattr("kenkui_server.compute.local.subprocess.Popen", spawn)
    try:
        runner._supervise()
        assert len(calls) == 1
        command, options = calls[0]
        assert ("kenkui_server.compute.managed_worker" in command) is managed
        assert (options["stdin"] is not None) is managed
        if managed:
            child.stdin.close.assert_called_once()
            assert repositories.jobs.get("job").status.value == (
                "cancel_requested" if running else "cancelled"
            )
        else:
            child.stdin.close.assert_not_called()
            assert repositories.jobs.get("job").status.value == ("running" if running else "queued")
    finally:
        database.close()


def test_cancellation_failure_still_closes_all_worker_control_pipes(tmp_path):
    runner = LocalProcessRunner(tmp_path / "db", tmp_path / "assets", stop_workers_on_close=True)
    repositories = Mock()
    repositories.request_cancellation.side_effect = RuntimeError("database unavailable")
    first, second = Mock(), Mock()
    with pytest.raises(RuntimeError, match="database unavailable"):
        runner._stop_children(repositories, [(first, "one", "token1"), (second, "two", "token2")])
    first.stdin.close.assert_called_once()
    second.stdin.close.assert_called_once()
    first.wait.assert_called_once()
    second.wait.assert_called_once()
