"""SQLite row mapping and authoritative repositories for local server state."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from kenkui_server.jobs.models import (
    Artifact,
    Asset,
    Casting,
    CharacterCasting,
    Dispatch,
    InspectedChapter,
    Inspection,
    Job,
    JobEvent,
    JobSpec,
    JobStatus,
    OutputSpec,
    Progress,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.storage.database import Database


class StaleWriteError(ValueError):
    """The stored snapshot changed since the caller read it."""

    def __init__(self) -> None:
        super().__init__("stale_write")


def _encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _encode_spec(spec: JobSpec) -> str:
    return _encode(
        {
            "source_id": spec.source_id,
            "chapters": spec.chapters,
            "casting": _casting_to_row(spec.casting),
            "tts": {"normalize_text": spec.tts.normalize_text},
            "output": asdict(spec.output),
        }
    )


def _decode_spec(raw: str) -> JobSpec:
    value: dict[str, Any] = json.loads(raw)
    return JobSpec(
        source_id=value["source_id"],
        chapters=tuple(value["chapters"]),
        casting=_casting_from_row(value["casting"]),
        tts=TtsSettings(value["tts"]["normalize_text"]),
        output=OutputSpec(**value["output"]),
    )


def _encode_progress(progress: Progress) -> str:
    return _encode(asdict(progress))


def _decode_progress(raw: str) -> Progress:
    value: dict[str, Any] = json.loads(raw)
    return Progress(value["stage"], value["completed"], value["total"])


def _job_from_row(row: tuple[str, str, str, int, str]) -> Job:
    return Job(
        id=row[0],
        spec=_decode_spec(row[1]),
        status=JobStatus(row[2]),
        version=row[3],
        progress=_decode_progress(row[4]),
    )


class AssetRepository:
    """Immutable local source asset records."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def put(self, asset: Asset) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO assets (id, path, sha256, format) VALUES (?, ?, ?, ?)",
                (asset.id, asset.path, asset.sha256, asset.format),
            )

    def get(self, asset_id: str) -> Asset:
        row = self._database.query(
            "SELECT id, path, sha256, format FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return Asset(*row)


class InspectionRepository:
    """Authoritative source inspection snapshots."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def put(self, inspection: Inspection) -> None:
        chapters = _encode([asdict(chapter) for chapter in inspection.chapters])
        with self._database.transaction() as connection:
            connection.execute(
                (
                    "INSERT INTO inspections (source_id, title, author, chapters_json) VALUES (?, "
                    "?, ?, ?)"
                ),
                (inspection.source_id, inspection.title, inspection.author, chapters),
            )

    def get(self, source_id: str) -> Inspection:
        row = self._database.query(
            "SELECT source_id, title, author, chapters_json FROM inspections WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            raise KeyError(source_id)
        chapters = tuple(InspectedChapter(**chapter) for chapter in json.loads(row[3]))
        return Inspection(row[0], row[1], row[2], chapters)


class JobRepository:
    """Authoritative job snapshots guarded by optimistic versions."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, job: Job) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                (
                    "INSERT INTO jobs (id, spec_json, status, version, progress_json) VALUES (?, "
                    "?, ?, ?, ?)"
                ),
                (
                    job.id,
                    _encode_spec(job.spec),
                    job.status.value,
                    job.version,
                    _encode_progress(job.progress),
                ),
            )

    def list(self) -> tuple[Job, ...]:
        """Return all authoritative snapshots in a deterministic order."""
        rows = self._database.query(
            "SELECT id, spec_json, status, version, progress_json FROM jobs ORDER BY id"
        ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def get(self, job_id: str) -> Job:
        row = self._database.query(
            "SELECT id, spec_json, status, version, progress_json FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job_from_row(row)

    def update(self, job: Job, *, expected_version: int) -> None:
        if job.version != expected_version + 1:
            raise ValueError("invalid_job_version")
        with self._database.transaction() as connection:
            stored_spec = connection.execute(
                "SELECT spec_json FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()
            if stored_spec is not None and _decode_spec(stored_spec[0]) != job.spec:
                raise ValueError("immutable_job_spec")
            result = connection.execute(
                """
                UPDATE jobs
                SET spec_json = ?, status = ?, version = ?, progress_json = ?
                WHERE id = ? AND version = ?
                """,
                (
                    _encode_spec(job.spec),
                    job.status.value,
                    job.version,
                    _encode_progress(job.progress),
                    job.id,
                    expected_version,
                ),
            )
            if result.rowcount != 1:
                raise StaleWriteError()


class JobEventRepository:
    """Append-only ordered job history."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def append(self, event: JobEvent) -> None:
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO job_events (job_id, sequence, event_type, progress_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        event.job_id,
                        event.sequence,
                        event.event_type,
                        _encode_progress(event.progress),
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "job_events.job_id, job_events.sequence" in str(error):
                raise ValueError("duplicate_event_sequence") from error
            raise

    def list_for_job(self, job_id: str) -> tuple[JobEvent, ...]:
        rows = self._database.query(
            """
            SELECT job_id, sequence, event_type, progress_json
            FROM job_events WHERE job_id = ? ORDER BY sequence
            """,
            (job_id,),
        ).fetchall()
        return tuple(JobEvent(row[0], row[1], row[2], _decode_progress(row[3])) for row in rows)


class DispatchRepository:
    """Durable local dispatch claims guarded by optimistic versions."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, dispatch: Dispatch) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO dispatches (id, job_id, status, version) VALUES (?, ?, ?, ?)",
                (dispatch.id, dispatch.job_id, dispatch.status, dispatch.version),
            )

    def get(self, dispatch_id: str) -> Dispatch:
        row = self._database.query(
            "SELECT id, job_id, status, version FROM dispatches WHERE id = ?", (dispatch_id,)
        ).fetchone()
        if row is None:
            raise KeyError(dispatch_id)
        return Dispatch(*row)

    def get_for_job(self, job_id: str) -> Dispatch:
        """Return the one durable dispatch associated with a local job."""
        row = self._database.query(
            "SELECT id, job_id, status, version FROM dispatches WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return Dispatch(*row)

    def list_pending(self) -> tuple[Dispatch, ...]:
        """Return unclaimed durable work for restart recovery."""
        rows = self._database.query(
            "SELECT id, job_id, status, version FROM dispatches WHERE status = "
            "'pending' ORDER BY id"
        ).fetchall()
        return tuple(Dispatch(*row) for row in rows)

    def list_incomplete(self) -> tuple[Dispatch, ...]:
        """Return pending or claimed work that must be reconciled after restart."""
        rows = self._database.query(
            """
            SELECT id, job_id, status, version FROM dispatches
            WHERE status IN ('pending', 'running') ORDER BY id
            """
        ).fetchall()
        return tuple(Dispatch(*row) for row in rows)

    def update(self, dispatch: Dispatch, *, expected_version: int) -> None:
        if dispatch.version != expected_version + 1:
            raise ValueError("invalid_dispatch_version")
        with self._database.transaction() as connection:
            result = connection.execute(
                """
                UPDATE dispatches SET status = ?, version = ? WHERE id = ? AND version = ?
                """,
                (dispatch.status, dispatch.version, dispatch.id, expected_version),
            )
            if result.rowcount != 1:
                raise StaleWriteError()


class ArtifactRepository:
    """Immutable published local artifact records."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def put(self, artifact: Artifact) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO artifacts (id, job_id, path, format) VALUES (?, ?, ?, ?)",
                (artifact.id, artifact.job_id, artifact.path, artifact.format),
            )

    def list_for_job(self, job_id: str) -> tuple[Artifact, ...]:
        rows = self._database.query(
            "SELECT id, job_id, path, format FROM artifacts WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return tuple(Artifact(*row) for row in rows)


class Repositories:
    """The complete repository set sharing one authoritative local database."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.assets = AssetRepository(database)
        self.inspections = InspectionRepository(database)
        self.jobs = JobRepository(database)
        self.events = JobEventRepository(database)
        self.dispatches = DispatchRepository(database)
        self.artifacts = ArtifactRepository(database)

    def create_job_and_dispatch(
        self, job: Job, dispatch: Dispatch, *, idempotency_key: str | None
    ) -> Job:
        """Atomically admit a queued job and its runnable dispatch before execution."""
        with self.database.transaction() as connection:
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT job_id FROM job_idempotency WHERE key = ?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    row = connection.execute(
                        (
                            "SELECT id, spec_json, status, version, progress_json FROM jobs "
                            "WHERE id = ?"
                        ),
                        (existing[0],),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("orphaned_idempotency_key")
                    existing_job = _job_from_row(row)
                    if existing_job.spec != job.spec:
                        raise ValueError("idempotency_conflict")
                    return existing_job
            connection.execute(
                (
                    "INSERT INTO jobs (id, spec_json, status, version, progress_json) VALUES (?, "
                    "?, ?, ?, ?)"
                ),
                (
                    job.id,
                    _encode_spec(job.spec),
                    job.status.value,
                    job.version,
                    _encode_progress(job.progress),
                ),
            )
            connection.execute(
                "INSERT INTO dispatches (id, job_id, status, version) VALUES (?, ?, ?, ?)",
                (dispatch.id, dispatch.job_id, dispatch.status, dispatch.version),
            )
            if idempotency_key is not None:
                connection.execute(
                    "INSERT INTO job_idempotency (key, job_id) VALUES (?, ?)",
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
        """Commit one optimistic snapshot transition and its next event together."""
        if job.version != expected_version + 1:
            raise ValueError("invalid_job_version")
        with self.database.transaction() as connection:
            self.check_lease(connection, lease)
            stored_spec = connection.execute(
                "SELECT spec_json FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()
            if stored_spec is not None and _decode_spec(stored_spec[0]) != job.spec:
                raise ValueError("immutable_job_spec")
            result = connection.execute(
                """
                UPDATE jobs
                SET status = ?, version = ?, progress_json = ?
                WHERE id = ? AND version = ?
                """,
                (
                    job.status.value,
                    job.version,
                    _encode_progress(job.progress),
                    job.id,
                    expected_version,
                ),
            )
            if result.rowcount != 1:
                raise StaleWriteError()
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM job_events WHERE job_id = ?",
                    (job.id,),
                ).fetchone()[0]
            )
            event = JobEvent(job.id, sequence, event_type, job.progress)
            connection.execute(
                """
                INSERT INTO job_events (job_id, sequence, event_type, progress_json)
                VALUES (?, ?, ?, ?)
                """,
                (event.job_id, event.sequence, event.event_type, _encode_progress(event.progress)),
            )
            if failure is not None:
                connection.execute(
                    "INSERT INTO job_failures (job_id, code, message) VALUES (?, ?, ?)",
                    (job.id, *failure),
                )
            if artifact is not None:
                if job.status is not JobStatus.SUCCEEDED or artifact.job_id != job.id:
                    raise ValueError("invalid_completion_artifact")
                connection.execute(
                    "INSERT INTO artifacts (id, job_id, path, format) VALUES (?, ?, ?, ?)",
                    (artifact.id, artifact.job_id, artifact.path, artifact.format),
                )
        return event

    def request_cancellation(self, job_id: str) -> Job:
        """Atomically admit cancellation without stranding a finished dispatch."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id, spec_json, status, version, progress_json FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = _job_from_row(row)
            if current.status.is_terminal or current.status is JobStatus.CANCEL_REQUESTED:
                return current

            dispatch = connection.execute(
                "SELECT status FROM dispatches WHERE job_id = ?", (job_id,)
            ).fetchone()
            if current.status is JobStatus.QUEUED:
                status = JobStatus.CANCELLED
                event_type = "cancelled"
            elif dispatch is not None and dispatch[0] not in {"pending", "running"}:
                status = JobStatus.CANCELLED
                event_type = "cancelled"
            else:
                status = JobStatus.CANCEL_REQUESTED
                event_type = "cancel_requested"
            cancelled = Job(
                current.id,
                current.spec,
                status=status,
                version=current.version + 1,
                progress=current.progress,
            )
            result = connection.execute(
                """
                UPDATE jobs SET status = ?, version = ?, progress_json = ?
                WHERE id = ? AND version = ?
                """,
                (
                    cancelled.status.value,
                    cancelled.version,
                    _encode_progress(cancelled.progress),
                    cancelled.id,
                    current.version,
                ),
            )
            if result.rowcount != 1:
                raise StaleWriteError()
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM job_events WHERE job_id = ?",
                    (cancelled.id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO job_events (job_id, sequence, event_type, progress_json)
                VALUES (?, ?, ?, ?)
                """,
                (cancelled.id, sequence, event_type, _encode_progress(cancelled.progress)),
            )
        return cancelled

    def finish_dispatch_if_not_cancellation_requested(
        self, dispatch: Dispatch, *, lease: tuple[str, str] | None = None
    ) -> bool:
        """Finish a dispatch only when its job was not durably cancelled first."""
        with self.database.transaction() as connection:
            self.check_lease(connection, lease)
            status = connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (dispatch.job_id,)
            ).fetchone()
            if status is None:
                raise KeyError(dispatch.job_id)
            if status[0] == JobStatus.CANCEL_REQUESTED.value:
                return False
            result = connection.execute(
                """
                UPDATE dispatches SET status = 'done', version = ? WHERE id = ? AND version = ?
                """,
                (dispatch.version + 1, dispatch.id, dispatch.version),
            )
            if result.rowcount != 1:
                raise StaleWriteError()
        return True

    @staticmethod
    def check_lease(connection: sqlite3.Connection, lease: tuple[str, str] | None) -> None:
        if lease is None:
            return
        row = connection.execute(
            "SELECT token, expires_at FROM execution_leases WHERE dispatch_id = ?", (lease[0],)
        ).fetchone()
        if row is None or row[0] != lease[1] or row[1] <= time.time():
            raise StaleWriteError()

    def claim_execution(
        self, dispatch_id: str, *, limit: int, ttl: float = 30, max_attempts: int = 3
    ) -> str | None:
        """Claim bounded work across API processes; only expired attempts may be replaced."""
        now = time.time()
        with self.database.transaction() as connection:
            row = connection.execute(
                (
                    "SELECT d.job_id, j.status FROM dispatches d JOIN jobs j ON j.id=d.job_id "
                    "WHERE d.id=?"
                ),
                (dispatch_id,),
            ).fetchone()
            if row is None or row[1] in {"succeeded", "failed", "cancelled"}:
                return None
            previous = connection.execute(
                "SELECT expires_at, attempts FROM execution_leases WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if previous is not None and previous[0] > now:
                return None
            active = connection.execute(
                (
                    "SELECT COUNT(*) FROM execution_leases l JOIN dispatches d ON "
                    "d.id=l.dispatch_id JOIN jobs j ON j.id=d.job_id WHERE l.expires_at>? AND "
                    "j.status NOT IN ('succeeded','failed','cancelled')"
                ),
                (now,),
            ).fetchone()[0]
            if active >= limit:
                return None
            if previous is not None and previous[1] >= max_attempts:
                status = "cancelled" if row[1] == "cancel_requested" else "failed"
                connection.execute(
                    "UPDATE jobs SET status=?, version=version+1 WHERE id=?", (status, row[0])
                )
                connection.execute(
                    "UPDATE dispatches SET status='done', version=version+1 WHERE id=?",
                    (dispatch_id,),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO job_failures VALUES (?, ?, ?)",
                    (row[0], "worker_lost", "The worker repeatedly stopped. Please retry the job."),
                )
                connection.execute(
                    (
                        "INSERT INTO job_events SELECT id, (SELECT "
                        "COALESCE(MAX(sequence),0)+1 FROM "
                        "job_events WHERE job_id=?), ?, progress_json FROM jobs WHERE id=?"
                    ),
                    (row[0], status, row[0]),
                )
                return None
            token = str(uuid4())
            connection.execute(
                (
                    "INSERT INTO execution_leases VALUES (?, ?, ?, 1) ON CONFLICT(dispatch_id) DO "
                    "UPDATE SET token=excluded.token, expires_at=excluded.expires_at, "
                    "attempts=execution_leases.attempts+1"
                ),
                (dispatch_id, token, now + ttl),
            )
            connection.execute(
                "UPDATE dispatches SET status='pending', version=version+1 WHERE id=?",
                (dispatch_id,),
            )
            return token

    def renew_execution(self, dispatch_id: str, token: str, *, ttl: float = 30) -> bool:
        with self.database.transaction() as connection:
            result = connection.execute(
                (
                    "UPDATE execution_leases SET expires_at=? WHERE dispatch_id=? AND token=? AND "
                    "expires_at>?"
                ),
                (time.time() + ttl, dispatch_id, token, time.time()),
            )
            return result.rowcount == 1

    def release_execution(self, dispatch_id: str, token: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE execution_leases SET expires_at=0 WHERE dispatch_id=? AND token=?",
                (dispatch_id, token),
            )

    def failure_for_job(self, job_id: str) -> dict[str, str] | None:
        row = self.database.query(
            "SELECT code, message FROM job_failures WHERE job_id=?", (job_id,)
        ).fetchone()
        return {"code": row[0], "message": row[1]} if row else None


def _casting_to_row(casting: Casting) -> dict[str, object]:
    """Serialise either casting shape, tagged by kind."""
    if isinstance(casting, CharacterCasting):
        return {
            "kind": "characters",
            "narrator_voice_id": casting.narrator_voice_id,
            "unknown_voice_id": casting.unknown_voice_id,
            "cast": [list(pair) for pair in casting.cast],
            "method": casting.method,
            "model_id": casting.model_id,
        }
    return {"kind": "single", "voice_id": casting.voice_id}


def _casting_from_row(value: dict[str, Any]) -> Casting:
    """Rebuild casting from a row.

    Rows written before character casting existed carry no kind, so a missing
    one means single. Without that, every stored job would fail to load.
    """
    if value.get("kind") != "characters":
        return SingleVoiceCasting(str(value["voice_id"]))
    return CharacterCasting(
        narrator_voice_id=str(value["narrator_voice_id"]),
        unknown_voice_id=str(value["unknown_voice_id"]),
        cast=tuple((str(a), str(b)) for a, b in value["cast"]),
        method=str(value["method"]),
        model_id=str(value["model_id"]),
    )
