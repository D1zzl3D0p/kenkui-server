# ruff: noqa: E501
"""PostgreSQL row-mapping repositories for hosted durable state.

The adapter accepts a DB-API-compatible connection so deployment chooses its
PostgreSQL driver without leaking that choice into core job semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID, uuid4

from kenkui_server.billing.models import (
    AuthorizationStatus,
    CreditAccount,
    CreditAuthorization,
    LedgerEntry,
)
from kenkui_server.jobs.models import Artifact, Dispatch, Job, JobEvent, JobStatus
from kenkui_server.storage.repositories import (
    StaleWriteError,
    _decode_progress,
    _decode_spec,
    _encode_progress,
    _encode_spec,
)


class PostgresCursor(Protocol):
    rowcount: int

    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> list[Mapping[str, Any]]: ...


class PostgresConnection(Protocol):
    def execute(self, statement: str, parameters: tuple[Any, ...]) -> PostgresCursor: ...

    def transaction(self) -> AbstractContextManager[PostgresConnection]: ...


def _job_from_row(row: Mapping[str, Any]) -> Job:
    return Job(
        id=str(row["id"]),
        spec=_decode_spec(str(row["spec_json"])),
        status=JobStatus(str(row["status"])),
        version=int(row["version"]),
        progress=_decode_progress(str(row["progress_json"])),
    )


def _authorization_from_row(row: Mapping[str, Any]) -> CreditAuthorization:
    return CreditAuthorization(
        str(row["id"]),
        str(row["job_id"]),
        str(row["account_id"]),
        int(row["credits"]),
        AuthorizationStatus(str(row["status"])),
    )


def _require_one(result: PostgresCursor) -> None:
    if result.rowcount != 1:
        raise StaleWriteError()


class PostgresJobRepository:
    """PostgreSQL snapshots guarded by the same optimistic versions as local state."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def create(self, job: Job) -> None:
        self._connection.execute(
            "INSERT INTO jobs (id, spec_json, status, version, progress_json) VALUES (%s, %s, %s, %s, %s)",
            (
                job.id,
                _encode_spec(job.spec),
                job.status.value,
                job.version,
                _encode_progress(job.progress),
            ),
        )

    def get(self, job_id: str) -> Job:
        row = self._connection.execute(
            """
            SELECT id, spec_json, status, version, progress_json
            FROM jobs WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job_from_row(row)

    def list(self) -> tuple[Job, ...]:
        rows = self._connection.execute(
            "SELECT id, spec_json, status, version, progress_json FROM jobs ORDER BY id", ()
        ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def update(self, job: Job, *, expected_version: int) -> None:
        if job.version != expected_version + 1:
            raise ValueError("invalid_job_version")
        result = self._connection.execute(
            """
            UPDATE jobs SET status = %s, version = %s, progress_json = %s
            WHERE id = %s AND version = %s
            """,
            (
                job.status.value,
                job.version,
                _encode_progress(job.progress),
                job.id,
                expected_version,
            ),
        )
        _require_one(result)

    def owner_id(self, job_id: str) -> UUID:
        row = self._connection.execute(
            "SELECT owner_id FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return UUID(str(row["owner_id"]))


class PostgresDispatchRepository:
    """PostgreSQL dispatch claims with conditional state changes."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def create(self, dispatch: Dispatch) -> None:
        self._connection.execute(
            "INSERT INTO dispatches (id, job_id, status, version) VALUES (%s, %s, %s, %s)",
            (dispatch.id, dispatch.job_id, dispatch.status, dispatch.version),
        )

    def update(self, dispatch: Dispatch, *, expected_version: int) -> None:
        if dispatch.version != expected_version + 1:
            raise ValueError("invalid_dispatch_version")
        result = self._connection.execute(
            """
            UPDATE dispatches SET status = %s, version = %s
            WHERE id = %s AND version = %s
            """,
            (dispatch.status, dispatch.version, dispatch.id, expected_version),
        )
        _require_one(result)

    def get_for_job(self, job_id: str) -> Dispatch:
        row = self._connection.execute(
            "SELECT id, job_id, status, version FROM dispatches WHERE job_id = %s", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return Dispatch(str(row["id"]), str(row["job_id"]), str(row["status"]), int(row["version"]))


class PostgresArtifactRepository:
    """Immutable hosted artifact publications."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def put(self, artifact: Artifact) -> None:
        result = self._connection.execute(
            """
            INSERT INTO artifacts (id, job_id, path, format)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (job_id) DO NOTHING
            """,
            (artifact.id, artifact.job_id, artifact.path, artifact.format),
        )
        _require_one(result)

    def list_for_job(self, job_id: str) -> tuple[Artifact, ...]:
        rows = self._connection.execute(
            "SELECT id, job_id, path, format FROM artifacts WHERE job_id = %s ORDER BY id",
            (job_id,),
        ).fetchall()
        return tuple(
            Artifact(str(row["id"]), str(row["job_id"]), str(row["path"]), str(row["format"]))
            for row in rows
        )


class PostgresRepositories:
    """Complete PostgreSQL job persistence with transaction-bound compound writes."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection
        self.jobs = PostgresJobRepository(connection)
        self.dispatches = PostgresDispatchRepository(connection)
        self.artifacts = PostgresArtifactRepository(connection)

    def create_job_and_dispatch(
        self, job: Job, dispatch: Dispatch, *, idempotency_key: str | None
    ) -> Job:
        with self._connection.transaction() as connection:
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT job_id FROM job_idempotency WHERE key = %s", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    row = connection.execute(
                        "SELECT id, spec_json, status, version, progress_json FROM jobs WHERE id = %s",
                        (str(existing["job_id"]),),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("orphaned_idempotency_key")
                    return _job_from_row(row)
            PostgresJobRepository(connection).create(job)
            PostgresDispatchRepository(connection).create(dispatch)
            if idempotency_key is not None:
                connection.execute(
                    "INSERT INTO job_idempotency (key, job_id) VALUES (%s, %s)",
                    (idempotency_key, job.id),
                )
        return job

    def update_job_and_append_event(
        self, job: Job, *, expected_version: int, event_type: str
    ) -> JobEvent:
        if job.version != expected_version + 1:
            raise ValueError("invalid_job_version")
        with self._connection.transaction() as connection:
            result = connection.execute(
                """
                UPDATE jobs SET status = %s, version = %s, progress_json = %s
                WHERE id = %s AND version = %s
                """,
                (
                    job.status.value,
                    job.version,
                    _encode_progress(job.progress),
                    job.id,
                    expected_version,
                ),
            )
            _require_one(result)
            sequence_row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM job_events WHERE job_id = %s",
                (job.id,),
            ).fetchone()
            if sequence_row is None:
                raise RuntimeError("missing_event_sequence")
            event = JobEvent(job.id, int(sequence_row["sequence"]), event_type, job.progress)
            connection.execute(
                "INSERT INTO job_events (job_id, sequence, event_type, progress_json) VALUES (%s, %s, %s, %s)",
                (event.job_id, event.sequence, event.event_type, _encode_progress(event.progress)),
            )
        return event


class PostgresHostedRepository(PostgresRepositories):
    """Hosted admission that reserves funds and makes work runnable atomically."""

    def admit(
        self,
        job: Job,
        dispatch: Dispatch,
        *,
        account_id: str,
        owner_id: str,
        credits: int,
        idempotency_key: str | None,
    ) -> Job:
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        with self._connection.transaction() as connection:
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT job_id FROM job_idempotency WHERE key = %s", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    row = connection.execute(
                        "SELECT id, spec_json, status, version, progress_json FROM jobs WHERE id = %s",
                        (str(existing["job_id"]),),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("orphaned_idempotency_key")
                    return _job_from_row(row)
            available = connection.execute(
                """
                UPDATE credit_accounts SET available_credits = available_credits - %s
                WHERE id = %s AND available_credits >= %s
                RETURNING id
                """,
                (credits, account_id, credits),
            ).fetchone()
            if available is None:
                raise ValueError("insufficient_credits")
            authorization_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO credit_authorizations (id, job_id, account_id, credits, status)
                VALUES (%s, %s, %s, %s, 'reserved')
                """,
                (authorization_id, job.id, account_id, credits),
            )
            connection.execute(
                """
                INSERT INTO credit_ledger_entries (id, account_id, authorization_id, kind, credits, reference)
                VALUES (%s, %s, %s, 'reservation', %s, %s)
                """,
                (str(uuid4()), account_id, authorization_id, -credits, job.id),
            )
            connection.execute(
                """
                INSERT INTO jobs (id, spec_json, status, version, progress_json, owner_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    job.id,
                    _encode_spec(job.spec),
                    job.status.value,
                    job.version,
                    _encode_progress(job.progress),
                    owner_id,
                ),
            )
            PostgresDispatchRepository(connection).create(dispatch)
            if idempotency_key is not None:
                connection.execute(
                    "INSERT INTO job_idempotency (key, job_id) VALUES (%s, %s)",
                    (idempotency_key, job.id),
                )
        return job


class PostgresBillingRepository:
    """Durable credit ledger and processed payment-event truth for hosted mode."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def account(self, account_id: str) -> CreditAccount:
        row = self._connection.execute(
            "SELECT id, available_credits FROM credit_accounts WHERE id = %s", (account_id,)
        ).fetchone()
        if row is None:
            raise KeyError(account_id)
        return CreditAccount(str(row["id"]), int(row["available_credits"]))

    def grant(self, account_id: str, credits: int, *, reference: str) -> CreditAccount:
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        with self._connection.transaction() as connection:
            return self._grant(connection, account_id, credits, reference)

    def process_payment_event(
        self, provider: str, provider_event_id: str, account_id: str, credits: int
    ) -> CreditAccount:
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        with self._connection.transaction() as connection:
            received = connection.execute(
                """
                INSERT INTO payment_events (provider, provider_event_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING provider_event_id
                """,
                (provider, provider_event_id),
            ).fetchone()
            if received is None:
                return self.account(account_id)
            return self._grant(connection, account_id, credits, f"{provider}:{provider_event_id}")

    def _grant(
        self, connection: PostgresConnection, account_id: str, credits: int, reference: str
    ) -> CreditAccount:
        inserted = connection.execute(
            """
            INSERT INTO credit_ledger_entries (id, account_id, authorization_id, kind, credits, reference)
            VALUES (%s, %s, NULL, 'purchase', %s, %s)
            ON CONFLICT (reference) DO NOTHING RETURNING id
            """,
            (str(uuid4()), account_id, credits, reference),
        ).fetchone()
        if inserted is None:
            return self.account(account_id)
        row = connection.execute(
            """
            UPDATE credit_accounts SET available_credits = available_credits + %s
            WHERE id = %s RETURNING id, available_credits
            """,
            (credits, account_id),
        ).fetchone()
        if row is None:
            raise KeyError(account_id)
        return CreditAccount(str(row["id"]), int(row["available_credits"]))

    def reserve(self, job_id: str, account_id: str, credits: int) -> CreditAuthorization:
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        with self._connection.transaction() as connection:
            existing = connection.execute(
                "SELECT id, job_id, account_id, credits, status FROM credit_authorizations WHERE job_id = %s",
                (job_id,),
            ).fetchone()
            if existing is not None:
                return _authorization_from_row(existing)
            available = connection.execute(
                """
                UPDATE credit_accounts SET available_credits = available_credits - %s
                WHERE id = %s AND available_credits >= %s RETURNING id
                """,
                (credits, account_id, credits),
            ).fetchone()
            if available is None:
                raise ValueError("insufficient_credits")
            authorization = CreditAuthorization(
                str(uuid4()), job_id, account_id, credits, AuthorizationStatus.RESERVED
            )
            connection.execute(
                "INSERT INTO credit_authorizations (id, job_id, account_id, credits, status) VALUES (%s, %s, %s, %s, %s)",
                (
                    authorization.id,
                    authorization.job_id,
                    authorization.account_id,
                    authorization.credits,
                    authorization.status.value,
                ),
            )
            connection.execute(
                """
                INSERT INTO credit_ledger_entries (id, account_id, authorization_id, kind, credits, reference)
                VALUES (%s, %s, %s, 'reservation', %s, %s)
                """,
                (str(uuid4()), account_id, authorization.id, -credits, job_id),
            )
            return authorization

    def authorization_for_job(self, job_id: str) -> CreditAuthorization:
        row = self._connection.execute(
            "SELECT id, job_id, account_id, credits, status FROM credit_authorizations WHERE job_id = %s",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _authorization_from_row(row)

    def finalize(self, job_id: str, status: AuthorizationStatus) -> CreditAuthorization:
        if status not in {AuthorizationStatus.SETTLED, AuthorizationStatus.RELEASED}:
            raise ValueError("invalid_final_authorization_status")
        with self._connection.transaction() as connection:
            current = connection.execute(
                "SELECT id, job_id, account_id, credits, status FROM credit_authorizations WHERE job_id = %s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if current is None:
                raise KeyError(job_id)
            authorization = _authorization_from_row(current)
            if authorization.status is not AuthorizationStatus.RESERVED:
                return authorization
            result = connection.execute(
                """
                UPDATE credit_authorizations SET status = %s, finalized_at = now()
                WHERE id = %s AND status = 'reserved'
                """,
                (status.value, authorization.id),
            )
            _require_one(result)
            if status is AuthorizationStatus.RELEASED:
                connection.execute(
                    "UPDATE credit_accounts SET available_credits = available_credits + %s WHERE id = %s",
                    (authorization.credits, authorization.account_id),
                )
                kind, credits = "release", authorization.credits
            else:
                kind, credits = "settlement", 0
            connection.execute(
                """
                INSERT INTO credit_ledger_entries (id, account_id, authorization_id, kind, credits, reference)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (str(uuid4()), authorization.account_id, authorization.id, kind, credits, job_id),
            )
            return CreditAuthorization(
                authorization.id,
                authorization.job_id,
                authorization.account_id,
                authorization.credits,
                status,
            )

    def ledger_for_authorization(self, authorization_id: str) -> tuple[LedgerEntry, ...]:
        rows = self._connection.execute(
            """
            SELECT id, account_id, authorization_id, kind, credits, reference, created_at
            FROM credit_ledger_entries WHERE authorization_id = %s ORDER BY created_at, id
            """,
            (authorization_id,),
        ).fetchall()
        return tuple(
            LedgerEntry(
                str(row["id"]),
                str(row["account_id"]),
                str(row["authorization_id"]),
                str(row["kind"]),
                int(row["credits"]),
                str(row["reference"]),
                row["created_at"],
            )
            for row in rows
        )
