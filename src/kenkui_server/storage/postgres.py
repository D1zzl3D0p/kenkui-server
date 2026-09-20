# ruff: noqa: E501
"""PostgreSQL row-mapping repositories for hosted durable state.

The adapter accepts a DB-API-compatible connection so deployment chooses its
PostgreSQL driver without leaking that choice into core job semantics.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID, uuid4

from kenkui_server.billing.models import (
    AuthorizationStatus,
    CreditAccount,
    CreditAuthorization,
    CreditLot,
    LedgerEntry,
)
from kenkui_server.jobs.models import (
    Artifact,
    Asset,
    Dispatch,
    InspectedChapter,
    Inspection,
    Job,
    JobEvent,
    JobStatus,
)
from kenkui_server.notifications.service import Recipient
from kenkui_server.storage.credit_lots import PostgresCreditLots
from kenkui_server.storage.postgres_execution import PostgresExecution
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
            (
                "INSERT INTO jobs (id, spec_json, status, version, progress_json) "
                "VALUES (%s, %s, %s, %s, %s)"
            ),
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


class PostgresIdentityRepository:
    """Stable provider-subject to internal-user mapping for hosted auth."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def user_id_for_subject(self, provider_subject: str, email: str | None = None) -> UUID:
        # The provider owns the address, so refresh it on every sign-in. COALESCE
        # keeps a stored address when a session happens to carry none.
        row = self._connection.execute(
            """
            INSERT INTO identities (id, workos_subject, email)
            VALUES (%s, %s, %s)
            ON CONFLICT (workos_subject)
            DO UPDATE SET email = COALESCE(EXCLUDED.email, identities.email)
            RETURNING id
            """,
            (str(uuid4()), provider_subject, email),
        ).fetchone()
        if row is None:
            raise RuntimeError("identity_upsert_failed")
        return UUID(str(row["id"]))

    def notification_settings(self, identity_id: UUID) -> tuple[str | None, bool]:
        """The address completion mail would reach, and whether it is wanted."""
        row = self._connection.execute(
            "SELECT email, notify_by_email FROM identities WHERE id = %s",
            (str(identity_id),),
        ).fetchone()
        if row is None:
            raise RuntimeError("unknown_identity")
        return row["email"], bool(row["notify_by_email"])

    def set_notify_by_email(self, identity_id: UUID, enabled: bool) -> None:
        self._connection.execute(
            "UPDATE identities SET notify_by_email = %s WHERE id = %s",
            (enabled, str(identity_id)),
        )


class PostgresNotificationRepository:
    """Recipients and at-most-once send records for completion mail."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def recipient_for_job(self, job_id: str) -> Recipient | None:
        row = self._connection.execute(
            """
            SELECT identities.id AS identity_id, identities.email, identities.notify_by_email
            FROM jobs JOIN identities ON identities.id = jobs.owner_id
            WHERE jobs.id = %s
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return Recipient(
            UUID(str(row["identity_id"])), row["email"], bool(row["notify_by_email"])
        )

    def claim(self, job_id: str, channel: str) -> bool:
        """Insert the send record first, so only one attempt may mail."""
        row = self._connection.execute(
            """
            INSERT INTO job_notifications (job_id, channel)
            VALUES (%s, %s)
            ON CONFLICT (job_id, channel) DO NOTHING
            RETURNING job_id
            """,
            (job_id, channel),
        ).fetchone()
        return row is not None

    def mark_delivered(self, job_id: str, channel: str) -> None:
        self._connection.execute(
            "UPDATE job_notifications SET delivered_at = now() WHERE job_id = %s AND channel = %s",
            (job_id, channel),
        )


class PostgresJobEventRepository:
    """Append-only hosted job history used for reconnectable event streams."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def append(self, event: JobEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO job_events (job_id, sequence, event_type, progress_json)
            VALUES (%s, %s, %s, %s)
            """,
            (event.job_id, event.sequence, event.event_type, _encode_progress(event.progress)),
        )

    def list_for_job(self, job_id: str) -> tuple[JobEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT job_id, sequence, event_type, progress_json
            FROM job_events WHERE job_id = %s ORDER BY sequence
            """,
            (job_id,),
        ).fetchall()
        return tuple(
            JobEvent(
                str(row["job_id"]),
                int(row["sequence"]),
                str(row["event_type"]),
                _decode_progress(str(row["progress_json"])),
            )
            for row in rows
        )


class PostgresAssetRepository:
    """Private hosted source metadata and durable ownership."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def put_for_owner(self, asset: Asset, owner_id: UUID) -> None:
        self._connection.execute(
            """
            INSERT INTO assets (id, path, sha256, format, owner_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (asset.id, asset.path, asset.sha256, asset.format, str(owner_id)),
        )

    def get(self, asset_id: str) -> Asset:
        row = self._connection.execute(
            "SELECT id, path, sha256, format FROM assets WHERE id = %s",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return Asset(str(row["id"]), str(row["path"]), str(row["sha256"]), str(row["format"]))

    def owner_id(self, asset_id: str) -> UUID:
        row = self._connection.execute(
            "SELECT owner_id FROM assets WHERE id = %s",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return UUID(str(row["owner_id"]))


class PostgresInspectionRepository:
    """Durable immutable hosted source inspections."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def put(self, inspection: Inspection) -> None:
        chapters = json.dumps(
            [{"id": chapter.id, "title": chapter.title} for chapter in inspection.chapters],
            separators=(",", ":"),
            sort_keys=True,
        )
        self._connection.execute(
            """
            INSERT INTO inspections (source_id, title, author, chapters_json)
            VALUES (%s, %s, %s, %s)
            """,
            (inspection.source_id, inspection.title, inspection.author, chapters),
        )

    def get(self, source_id: str) -> Inspection:
        row = self._connection.execute(
            "SELECT source_id, title, author, chapters_json FROM inspections WHERE source_id = %s",
            (source_id,),
        ).fetchone()
        if row is None:
            raise KeyError(source_id)
        chapters = tuple(
            InspectedChapter(**chapter) for chapter in json.loads(str(row["chapters_json"]))
        )
        return Inspection(str(row["source_id"]), str(row["title"]), str(row["author"]), chapters)


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

    def get(self, dispatch_id: str) -> Dispatch:
        row = self._connection.execute(
            "SELECT id, job_id, status, version FROM dispatches WHERE id = %s",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            raise KeyError(dispatch_id)
        return Dispatch(str(row["id"]), str(row["job_id"]), str(row["status"]), int(row["version"]))

    def get_for_job(self, job_id: str) -> Dispatch:
        row = self._connection.execute(
            "SELECT id, job_id, status, version FROM dispatches WHERE job_id = %s",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return Dispatch(str(row["id"]), str(row["job_id"]), str(row["status"]), int(row["version"]))

    def list_pending(self) -> tuple[Dispatch, ...]:
        rows = self._connection.execute(
            """
            SELECT id, job_id, status, version FROM dispatches
            WHERE status = 'pending' ORDER BY id
            """,
            (),
        ).fetchall()
        return tuple(
            Dispatch(str(row["id"]), str(row["job_id"]), str(row["status"]), int(row["version"]))
            for row in rows
        )

    def list_incomplete(self) -> tuple[Dispatch, ...]:
        rows = self._connection.execute(
            """
            SELECT d.id, d.job_id, d.status, d.version FROM dispatches d
            JOIN jobs j ON j.id = d.job_id
            WHERE j.status NOT IN ('succeeded', 'failed', 'cancelled') ORDER BY d.id
            """,
            (),
        ).fetchall()
        return tuple(
            Dispatch(str(row["id"]), str(row["job_id"]), str(row["status"]), int(row["version"]))
            for row in rows
        )


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


class PostgresRepositories(PostgresExecution):
    """Complete PostgreSQL job persistence with transaction-bound compound writes."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection
        self.jobs = PostgresJobRepository(connection)
        self.events = PostgresJobEventRepository(connection)
        self.assets = PostgresAssetRepository(connection)
        self.inspections = PostgresInspectionRepository(connection)
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
                        (
                            "SELECT id, spec_json, status, version, progress_json FROM jobs "
                            "WHERE id = %s"
                        ),
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
        self,
        job: Job,
        *,
        expected_version: int,
        event_type: str,
        artifact: Artifact | None = None,
        lease: tuple[str, str] | None = None,
        failure: tuple[str, str] | None = None,
    ) -> JobEvent:
        with self._connection.transaction() as connection:
            self.check_lease(connection, lease)
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
                (
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM job_events "
                    "WHERE job_id = %s"
                ),
                (job.id,),
            ).fetchone()
            if sequence_row is None:
                raise RuntimeError("missing_event_sequence")
            event = JobEvent(job.id, int(sequence_row["sequence"]), event_type, job.progress)
            connection.execute(
                (
                    "INSERT INTO job_events (job_id, sequence, event_type, "
                    "progress_json) VALUES (%s, %s, %s, %s)"
                ),
                (event.job_id, event.sequence, event.event_type, _encode_progress(event.progress)),
            )
            if artifact is not None:
                if job.status is not JobStatus.SUCCEEDED or artifact.job_id != job.id:
                    raise ValueError("invalid_completion_artifact")
                connection.execute(
                    "INSERT INTO artifacts (id, job_id, path, format) VALUES (%s,%s,%s,%s)",
                    (artifact.id, artifact.job_id, artifact.path, artifact.format),
                )
            if failure is not None:
                connection.execute("INSERT INTO job_failures VALUES (%s,%s,%s)", (job.id, *failure))
            billing = getattr(self, "billing", None)
            if job.status.is_terminal:
                connection.execute("UPDATE jobs SET terminal_at=now() WHERE id=%s", (job.id,))
            if billing is not None and job.status.is_terminal:
                billing.finalize(
                    job.id,
                    AuthorizationStatus.SETTLED
                    if job.status is JobStatus.SUCCEEDED
                    else AuthorizationStatus.RELEASED,
                )
        return event

    def request_cancellation(self, job_id: str) -> Job:
        """Atomically record hosted cancellation and its matching event."""
        with self._connection.transaction() as connection:
            row = connection.execute(
                "SELECT id, spec_json, status, version, progress_json FROM jobs WHERE id = %s",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = _job_from_row(row)
            if current.status.is_terminal or current.status is JobStatus.CANCEL_REQUESTED:
                return current
            dispatch = connection.execute(
                "SELECT status FROM dispatches WHERE job_id = %s",
                (job_id,),
            ).fetchone()
            if current.status is JobStatus.QUEUED or (
                dispatch is not None and str(dispatch["status"]) not in {"pending", "running"}
            ):
                status, event_type = JobStatus.CANCELLED, "cancelled"
            else:
                status, event_type = JobStatus.CANCEL_REQUESTED, "cancel_requested"
            cancelled = Job(
                current.id,
                current.spec,
                status=status,
                version=current.version + 1,
                progress=current.progress,
            )
            result = connection.execute(
                """
                UPDATE jobs SET status = %s, version = %s, progress_json = %s
                WHERE id = %s AND version = %s
                """,
                (
                    cancelled.status.value,
                    cancelled.version,
                    _encode_progress(cancelled.progress),
                    cancelled.id,
                    current.version,
                ),
            )
            _require_one(result)
            sequence = connection.execute(
                (
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM job_events "
                    "WHERE job_id = %s"
                ),
                (job_id,),
            ).fetchone()
            if sequence is None:
                raise RuntimeError("missing_event_sequence")
            connection.execute(
                """
                INSERT INTO job_events (job_id, sequence, event_type, progress_json)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    job_id,
                    int(sequence["sequence"]),
                    event_type,
                    _encode_progress(cancelled.progress),
                ),
            )
            if cancelled.status.is_terminal:
                connection.execute("UPDATE jobs SET terminal_at=now() WHERE id=%s", (job_id,))
            if cancelled.status.is_terminal and hasattr(self, "billing"):
                self.billing.finalize(job_id, AuthorizationStatus.RELEASED)
        return cancelled

    def finish_dispatch_if_not_cancellation_requested(
        self, dispatch: Dispatch, *, lease: tuple[str, str] | None = None
    ) -> bool:
        """Finish terminal jobs; return False if cancellation still needs acknowledgement."""
        with self._connection.transaction() as connection:
            self.check_lease(connection, lease)
            status = connection.execute(
                "SELECT status FROM jobs WHERE id = %s",
                (dispatch.job_id,),
            ).fetchone()
            if status is None:
                raise KeyError(dispatch.job_id)
            if str(status["status"]) == JobStatus.CANCEL_REQUESTED.value:
                return False
            if not JobStatus(str(status["status"])).is_terminal:
                raise StaleWriteError()
            result = connection.execute(
                """
                UPDATE dispatches SET status = 'done', version = %s
                WHERE id = %s AND version = %s
                """,
                (dispatch.version + 1, dispatch.id, dispatch.version),
            )
            _require_one(result)
        return True


class PostgresHostedRepository(PostgresRepositories):
    """Hosted admission that reserves funds and makes work runnable atomically."""

    def __init__(self, connection: PostgresConnection) -> None:
        super().__init__(connection)
        self.billing = PostgresBillingRepository(connection)

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
            # Serialize admissions for an account before checking replay or balance.
            connection.execute(
                "SELECT id FROM credit_accounts WHERE id = %s FOR UPDATE", (account_id,)
            )
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT job_id FROM job_idempotency WHERE key = %s", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    row = connection.execute(
                        (
                            "SELECT id, spec_json, status, version, progress_json FROM jobs "
                            "WHERE id = %s"
                        ),
                        (str(existing["job_id"]),),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("orphaned_idempotency_key")
                    existing_job = _job_from_row(row)
                    if existing_job.spec != job.spec:
                        raise ValueError("idempotency_conflict")
                    return existing_job
            active = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE owner_id=%s AND status NOT IN ('succeeded','failed','cancelled')",
                (owner_id,),
            ).fetchone()
            if active is not None and int(active["count"]) >= 3:
                raise ValueError("active_job_limit")
            source = connection.execute(
                "SELECT id, deleted_at FROM assets WHERE id=%s FOR UPDATE", (job.spec.source_id,)
            ).fetchone()
            if source is not None and source.get("deleted_at") is not None:
                raise ValueError("source_expired")
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
            PostgresCreditLots(connection).reserve(authorization_id, account_id, credits)
            connection.execute(
                """
                INSERT INTO credit_ledger_entries (id, account_id, authorization_id, kind, credits, reference)
                VALUES (%s, %s, %s, 'reservation', %s, %s)
                """,
                (str(uuid4()), account_id, authorization_id, -credits, f"reservation:{job.id}"),
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

    def credit_lots(self, account_id: str) -> tuple[CreditLot, ...]:
        return PostgresCreditLots(self._connection).list(account_id)

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
            connection.execute(
                """
                INSERT INTO payment_events (provider, provider_event_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING provider_event_id
                """,
                (provider, provider_event_id),
            ).fetchone()
            return self._grant(
                connection, account_id, credits, f"{provider}:{provider_event_id}", kind="purchase"
            )

    def _grant(
        self, connection: PostgresConnection, account_id: str, credits: int, reference: str,
        *, kind: str = "grant",
    ) -> CreditAccount:
        connection.execute("SELECT id FROM credit_accounts WHERE id=%s FOR UPDATE", (account_id,))
        inserted = connection.execute(
            """
            INSERT INTO credit_ledger_entries (id, account_id, authorization_id, kind, credits, reference)
            VALUES (%s, %s, NULL, 'purchase', %s, %s)
            ON CONFLICT (reference) DO NOTHING RETURNING id
            """,
            (str(uuid4()), account_id, credits, reference),
        ).fetchone()
        if inserted is None:
            existing = connection.execute(
                "SELECT account_id, credits FROM credit_ledger_entries WHERE reference=%s",
                (reference,),
            ).fetchone()
            if existing is None or (str(existing["account_id"]), int(existing["credits"])) != (
                account_id, credits
            ):
                raise ValueError("payment_reference_conflict")
            return self.account(account_id)
        PostgresCreditLots(connection).grant(account_id, credits, reference, kind)
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
            connection.execute(
                "SELECT id FROM credit_accounts WHERE id=%s FOR UPDATE", (account_id,)
            )
            existing = connection.execute(
                (
                    "SELECT id, job_id, account_id, credits, status FROM "
                    "credit_authorizations WHERE job_id = %s"
                ),
                (job_id,),
            ).fetchone()
            if existing is not None:
                authorization = _authorization_from_row(existing)
                if (authorization.account_id, authorization.credits) != (account_id, credits):
                    raise ValueError("authorization_conflict")
                return authorization
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
                (
                    "INSERT INTO credit_authorizations (id, job_id, account_id, "
                    "credits, status) VALUES (%s, %s, %s, %s, %s)"
                ),
                (
                    authorization.id,
                    authorization.job_id,
                    authorization.account_id,
                    authorization.credits,
                    authorization.status.value,
                ),
            )
            PostgresCreditLots(connection).reserve(authorization.id, account_id, credits)
            connection.execute(
                """
                INSERT INTO credit_ledger_entries (id, account_id, authorization_id, kind, credits, reference)
                VALUES (%s, %s, %s, 'reservation', %s, %s)
                """,
                (str(uuid4()), account_id, authorization.id, -credits, f"reservation:{job_id}"),
            )
            return authorization

    def authorization_for_job(self, job_id: str) -> CreditAuthorization:
        row = self._connection.execute(
            (
                "SELECT id, job_id, account_id, credits, status FROM "
                "credit_authorizations WHERE job_id = %s"
            ),
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _authorization_from_row(row)

    def finalize(self, job_id: str, status: AuthorizationStatus) -> CreditAuthorization:
        if status not in {AuthorizationStatus.SETTLED, AuthorizationStatus.RELEASED}:
            raise ValueError("invalid_final_authorization_status")
        with self._connection.transaction() as connection:
            account_id = self.authorization_for_job(job_id).account_id
            connection.execute(
                "SELECT id FROM credit_accounts WHERE id=%s FOR UPDATE", (account_id,)
            )
            current = connection.execute(
                (
                    "SELECT id, job_id, account_id, credits, status FROM "
                    "credit_authorizations WHERE job_id = %s FOR UPDATE"
                ),
                (job_id,),
            ).fetchone()
            if current is None:
                raise KeyError(job_id)
            authorization = _authorization_from_row(current)
            if authorization.status is not AuthorizationStatus.RESERVED:
                return authorization
            PostgresCreditLots(connection).finalize(
                authorization.id, authorization.account_id, authorization.credits, status
            )
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
                    (
                        "UPDATE credit_accounts SET available_credits = available_credits "
                        "+ %s WHERE id = %s"
                    ),
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
                (
                    str(uuid4()),
                    authorization.account_id,
                    authorization.id,
                    kind,
                    credits,
                    f"{kind}:{job_id}",
                ),
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
