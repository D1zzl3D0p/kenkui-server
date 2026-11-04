import hashlib
import json
from unittest.mock import Mock
from uuid import uuid4

import modal
import pytest

from kenkui_server.compute.modal import ModalProcessRunner


@pytest.fixture
def submission(monkeypatch):
    dispatch_id, token = str(uuid4()), str(uuid4())
    repository = Mock()
    repository.claim_execution.return_value = token
    function = Mock()
    function.spawn.return_value = Mock(spec=modal.FunctionCall, object_id="fc-01EXAMPLE")
    monkeypatch.setattr(modal.Function, "from_name", lambda *args, **kwargs: function)
    return ModalProcessRunner(repository), dispatch_id, token, function.spawn


def test_spawn_correlates_returned_call_with_exact_submitted_lease(submission, capsys, caplog):
    runner, dispatch_id, token, spawn = submission
    assert runner.start(dispatch_id) is None
    spawn.assert_called_once_with(dispatch_id, token)
    out, err = capsys.readouterr()
    assert token not in out + err + caplog.text
    assert json.loads(out) == {
        "event": "hosted_dispatch_spawned",
        "dispatch_id": dispatch_id,
        "function_call_id": "fc-01EXAMPLE",
        "lease_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
    }


@pytest.mark.parametrize(
    "outcome", ["no-lease", "spawn-error", "no-handle", "wrong-handle", "bad-id"]
)
def test_uncorrelated_spawn_emits_no_event(submission, outcome, capsys, caplog):
    runner, dispatch_id, token, spawn = submission
    if outcome == "no-lease":
        runner.repositories.claim_execution.return_value = None
    elif outcome == "spawn-error":
        spawn.side_effect = TimeoutError("ambiguous submission")
    elif outcome == "no-handle":
        spawn.return_value = None
    elif outcome == "wrong-handle":
        spawn.return_value = Mock(object_id="fc-01EXAMPLE")
    else:
        spawn.return_value.object_id = "fc-unsafe\nmetadata"
    assert runner.start(dispatch_id) is None
    assert spawn.call_count == (0 if outcome == "no-lease" else 1)
    out, err = capsys.readouterr()
    assert not out
    assert "hosted_dispatch_spawned" not in err + caplog.text
    assert token not in err + caplog.text


def test_correlation_output_failure_keeps_successful_spawn(submission, monkeypatch, caplog):
    runner, dispatch_id, token, spawn = submission
    write = Mock(side_effect=OSError("output unavailable"))
    with monkeypatch.context() as patch:
        patch.setattr("sys.stdout.write", write)
        assert runner.start(dispatch_id) is None
    write.assert_called_once()
    spawn.assert_called_once_with(dispatch_id, token)
    runner.repositories.claim_execution.assert_called_once_with(dispatch_id, limit=2, ttl=900)
    assert not caplog.records
