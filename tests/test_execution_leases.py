from dataclasses import replace

import pytest

from kenkui_server.jobs.models import (
    Artifact,
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.jobs.transitions import Completed, DispatchRequested, transition
from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories, StaleWriteError


def seed(repo, name):
    spec = JobSpec(
        "source", ("chapter",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("out.m4b")
    )
    job = Job(name, spec)
    repo.create_job_and_dispatch(job, Dispatch(name, name, "pending"), idempotency_key=None)
    return job


def test_claims_enforce_concurrency_across_database_connections(tmp_path):
    first = Repositories(Database(tmp_path / "state.sqlite3"))
    second = Repositories(Database(tmp_path / "state.sqlite3"))
    seed(first, "one")
    seed(first, "two")
    token = first.claim_execution("one", limit=1)
    assert token
    assert second.claim_execution("one", limit=1) is None
    assert second.claim_execution("two", limit=1) is None
    first.release_execution("one", token)
    assert second.claim_execution("two", limit=1)


def test_expired_attempt_cannot_publish_or_renew(tmp_path):
    repo = Repositories(Database(tmp_path / "state.sqlite3"))
    job = seed(repo, "one")
    old = repo.claim_execution("one", limit=1)
    running = transition(job, DispatchRequested())
    repo.update_job_and_append_event(
        running, expected_version=0, event_type="running", lease=("one", old)
    )
    repo.release_execution("one", old)
    new = repo.claim_execution("one", limit=1)
    assert new != old
    assert not repo.renew_execution("one", old)
    with pytest.raises(StaleWriteError):
        repo.update_job_and_append_event(
            transition(running, Completed()),
            expected_version=1,
            event_type="completed",
            artifact=Artifact("a", "one", "old.m4b", "m4b"),
            lease=("one", old),
        )
    assert repo.jobs.get("one") == running
    assert repo.artifacts.list_for_job("one") == ()
    assert repo.renew_execution("one", new)


def test_repeated_worker_loss_terminalizes_after_bounded_attempts(tmp_path):
    repo = Repositories(Database(tmp_path / "state.sqlite3"))
    seed(repo, "one")
    for _ in range(3):
        token = repo.claim_execution("one", limit=1)
        assert token
        repo.release_execution("one", token)
    assert repo.claim_execution("one", limit=1) is None
    assert repo.jobs.get("one").status.value == "failed"
    assert repo.failure_for_job("one")["code"] == "worker_lost"
    assert repo.events.list_for_job("one")[-1].event_type == "failed"


def test_reusing_idempotency_key_with_changed_intent_is_rejected(tmp_path):
    repo = Repositories(Database(tmp_path / "state.sqlite3"))
    job = seed(repo, "one")
    repo.create_job_and_dispatch(
        replace(job, id="two"), Dispatch("two", "two", "pending"), idempotency_key="key"
    )
    different = replace(
        job, id="three", spec=replace(job.spec, output=OutputSpec("out.m4b", title="Changed"))
    )
    with pytest.raises(ValueError, match="idempotency_conflict"):
        repo.create_job_and_dispatch(
            different, Dispatch("three", "three", "pending"), idempotency_key="key"
        )
