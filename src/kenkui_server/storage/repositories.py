"""SQLite row mapping and authoritative repositories for local server state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from kenkui_server.jobs.models import (
    Artifact,
    Asset,
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
            "casting": {"voice_id": spec.casting.voice_id},
            "tts": {"normalize_text": spec.tts.normalize_text},
            "output": {"path": spec.output.path},
        }
    )


def _decode_spec(raw: str) -> JobSpec:
    value: dict[str, Any] = json.loads(raw)
    return JobSpec(
        source_id=value["source_id"],
        chapters=tuple(value["chapters"]),
        casting=SingleVoiceCasting(value["casting"]["voice_id"]),
        tts=TtsSettings(value["tts"]["normalize_text"]),
        output=OutputSpec(value["output"]["path"]),
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
        row = self._database.connection.execute(
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
                "INSERT INTO inspections (source_id, title, author, chapters_json) VALUES (?, ?, ?, ?)",
                (inspection.source_id, inspection.title, inspection.author, chapters),
            )

    def get(self, source_id: str) -> Inspection:
        row = self._database.connection.execute(
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
                "INSERT INTO jobs (id, spec_json, status, version, progress_json) VALUES (?, ?, ?, ?, ?)",
                (job.id, _encode_spec(job.spec), job.status.value, job.version, _encode_progress(job.progress)),
            )

    def list(self) -> tuple[Job, ...]:
        """Return all authoritative snapshots in a deterministic order."""
        rows = self._database.connection.execute(
            "SELECT id, spec_json, status, version, progress_json FROM jobs ORDER BY id"
        ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def get(self, job_id: str) -> Job:
        row = self._database.connection.execute(
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
                    (event.job_id, event.sequence, event.event_type, _encode_progress(event.progress)),
                )
        except sqlite3.IntegrityError as error:
            if "job_events.job_id, job_events.sequence" in str(error):
                raise ValueError("duplicate_event_sequence") from error
            raise

    def list_for_job(self, job_id: str) -> tuple[JobEvent, ...]:
        rows = self._database.connection.execute(
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
        row = self._database.connection.execute(
            "SELECT id, job_id, status, version FROM dispatches WHERE id = ?", (dispatch_id,)
        ).fetchone()
        if row is None:
            raise KeyError(dispatch_id)
        return Dispatch(*row)

    def get_for_job(self, job_id: str) -> Dispatch:
        """Return the one durable dispatch associated with a local job."""
        row = self._database.connection.execute(
            "SELECT id, job_id, status, version FROM dispatches WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return Dispatch(*row)

    def list_pending(self) -> tuple[Dispatch, ...]:
        """Return unclaimed durable work for restart recovery."""
        rows = self._database.connection.execute(
            "SELECT id, job_id, status, version FROM dispatches WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        return tuple(Dispatch(*row) for row in rows)

    def list_incomplete(self) -> tuple[Dispatch, ...]:
        """Return pending or claimed work that must be reconciled after restart."""
        rows = self._database.connection.execute(
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
        rows = self._database.connection.execute(
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
                        "SELECT id, spec_json, status, version, progress_json FROM jobs WHERE id = ?",
                        (existing[0],),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("orphaned_idempotency_key")
                    return _job_from_row(row)
            connection.execute(
                "INSERT INTO jobs (id, spec_json, status, version, progress_json) VALUES (?, ?, ?, ?, ?)",
                (job.id, _encode_spec(job.spec), job.status.value, job.version, _encode_progress(job.progress)),
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
        self, job: Job, *, expected_version: int, event_type: str
    ) -> JobEvent:
        """Commit one optimistic snapshot transition and its next event together."""
        if job.version != expected_version + 1:
            raise ValueError("invalid_job_version")
        with self.database.transaction() as connection:
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
        return event

    def finish_dispatch_if_not_cancellation_requested(self, dispatch: Dispatch) -> bool:
        """Finish a dispatch only when its job was not durably cancelled first."""
        with self.database.transaction() as connection:
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
