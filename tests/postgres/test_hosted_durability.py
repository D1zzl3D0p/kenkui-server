from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

import pytest

from kenkui_server.jobs.models import (
    Artifact,
    Asset,
    Dispatch,
    Job,
    JobEvent,
    JobSpec,
    JobStatus,
    OutputSpec,
    Progress,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.storage.postgres import (
    PostgresHostedRepository,
    PostgresIdentityRepository,
    PostgresRepositories,
)
from kenkui_server.storage.repositories import _encode_spec


class Cursor:
    def __init__(self, row: dict[str, object] | None = None, *, rowcount: int = 1) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, object] | None:
        return self._row

    def fetchall(self) -> list[dict[str, object]]:
        return [self._row] if self._row is not None else []


class RecordingConnection:
    def __init__(self, *, insufficient_credits: bool = False) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.transactions = 0
        self.insufficient_credits = insufficient_credits

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield self

    def execute(self, statement: str, parameters: tuple[object, ...]) -> Cursor:
        self.statements.append((statement, parameters))
        normalized = " ".join(statement.split())
        if "credited-reserved-consumed AS available" in normalized:
            return Cursor({"id": "grant-1", "available": 10})
        if "available_credits = available_credits -" in normalized:
            return Cursor(None if self.insufficient_credits else {"id": "account-1"})
        if "SELECT COALESCE(MAX(sequence)" in normalized:
            return Cursor({"sequence": 1})
        if "SELECT spec_json" in normalized:
            return Cursor({"spec_json": _encode_spec(_job().spec)})
        if "INSERT INTO identities" in normalized:
            return Cursor({"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})
        if normalized.startswith("SELECT status FROM jobs"):
            return Cursor({"status": "running"})
        return Cursor()


def _job() -> Job:
    return Job(
        "job-1",
        JobSpec(
            source_id="source-1",
            chapters=("chapter-1",),
            casting=SingleVoiceCasting("voice-1"),
            tts=TtsSettings(),
            output=OutputSpec("private.m4b"),
        ),
        progress=Progress("queued", 0, 1),
    )


def test_hosted_admission_reserves_credit_job_and_dispatch_in_one_database_transaction() -> None:
    connection = RecordingConnection()
    job = _job()
    dispatch = Dispatch("dispatch-1", job.id, "pending")

    admitted = PostgresHostedRepository(connection).admit(
        job,
        dispatch,
        account_id="account-1",
        owner_id="owner-1",
        credits=1,
        idempotency_key="request-1",
    )

    assert admitted == job
    assert connection.transactions == 1
    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "credit_authorizations" in sql
    assert "INSERT INTO jobs" in sql
    assert "INSERT INTO dispatches" in sql
    assert "INSERT INTO job_idempotency" in sql


def test_hosted_admission_does_not_create_job_when_conditional_credit_reservation_fails() -> None:
    connection = RecordingConnection(insufficient_credits=True)

    with pytest.raises(ValueError, match="insufficient_credits"):
        PostgresHostedRepository(connection).admit(
            _job(),
            Dispatch("dispatch-1", "job-1", "pending"),
            account_id="account-1",
            owner_id="owner-1",
            credits=1,
            idempotency_key=None,
        )

    assert connection.transactions == 1
    assert all("INSERT INTO jobs" not in statement for statement, _ in connection.statements)


def test_postgres_repositories_guard_job_dispatch_and_artifact_mutations_by_expected_version() -> (
    None
):
    connection = RecordingConnection()
    repositories = PostgresRepositories(connection)
    job = _job()
    repositories.create_job_and_dispatch(
        job, Dispatch("dispatch-1", job.id, "pending"), idempotency_key=None
    )
    running = Job(job.id, job.spec, JobStatus.RUNNING, 1, Progress("running", 0, 1))
    repositories.update_job_and_append_event(running, expected_version=0, event_type="running")
    repositories.dispatches.update(Dispatch("dispatch-1", job.id, "running", 1), expected_version=0)
    repositories.artifacts.put(Artifact("artifact-1", job.id, "private.m4b", "m4b"))

    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "WHERE id = %s AND version = %s" in sql
    assert "WHERE id = %s AND version = %s" in sql
    assert "INSERT INTO artifacts" in sql
    assert "ON CONFLICT (job_id) DO NOTHING" in sql


def test_postgres_repositories_expose_event_and_dispatch_recovery_operations() -> None:
    connection = RecordingConnection()
    repositories = PostgresRepositories(connection)
    event = JobEvent("job-1", 1, "queued", Progress("queued", 0, 1))

    repositories.events.append(event)
    assert repositories.events.list_for_job("job-1") == ()
    assert repositories.dispatches.list_pending() == ()
    assert repositories.dispatches.list_incomplete() == ()

    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "INSERT INTO job_events" in sql
    assert "FROM job_events WHERE job_id = %s ORDER BY sequence" in sql
    assert "WHERE status = 'pending'" in sql
    assert "WHERE j.status NOT IN ('succeeded', 'failed', 'cancelled')" in sql


def test_postgres_worker_finishes_a_dispatch_only_when_cancellation_was_not_requested() -> None:
    connection = RecordingConnection()
    repositories = PostgresRepositories(connection)

    from kenkui_server.storage.repositories import StaleWriteError

    with pytest.raises(StaleWriteError):
        repositories.finish_dispatch_if_not_cancellation_requested(
            Dispatch("dispatch-1", "job-1", "running", 1)
        )
    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "UPDATE dispatches SET status = 'done'" not in sql


def test_postgres_assets_persist_the_owner_used_by_hosted_authorization() -> None:
    connection = RecordingConnection()
    repositories = PostgresRepositories(connection)

    repositories.assets.put_for_owner(
        Asset("source-1", "private/source-1.epub", "digest", "epub"),
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    statement, parameters = connection.statements[-1]
    assert "INSERT INTO assets (id, path, sha256, format, owner_id)" in statement
    assert parameters[-1] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_postgres_identity_mapping_is_durable_and_returns_a_stable_uuid() -> None:
    connection = RecordingConnection()

    identity_id = PostgresIdentityRepository(connection).user_id_for_subject("workos-user-1")

    assert identity_id == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    assert "ON CONFLICT (workos_subject)" in connection.statements[-1][0]
