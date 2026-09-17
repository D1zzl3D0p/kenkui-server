"""Idempotent retention policy for hosted private storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class RetentionKind(StrEnum):
    TEMPORARY = "temporary"
    SOURCE = "source"
    ARTIFACT = "artifact"


@dataclass(frozen=True, slots=True)
class RetentionRecord:
    identifier: str
    kind: RetentionKind
    terminal_at: datetime
    all_dependent_jobs_terminal: bool = True


class RetentionRepository(Protocol):
    def candidates(self) -> tuple[RetentionRecord, ...]: ...

    def delete(self, record: RetentionRecord) -> None: ...


class RetentionWorker:
    """Deletes eligible records safely on every retry after a failed run."""

    temporary_for = timedelta(hours=24)
    source_for = timedelta(hours=24)
    artifact_for = timedelta(days=30)

    def __init__(self, repository: RetentionRepository) -> None:
        self._repository = repository

    def run(self, now: datetime) -> tuple[str, ...]:
        deleted: list[str] = []
        for record in sorted(self._repository.candidates(), key=lambda item: item.identifier):
            if self._expired(record, now):
                self._repository.delete(record)
                deleted.append(record.identifier)
        return tuple(deleted)

    def _expired(self, record: RetentionRecord, now: datetime) -> bool:
        if record.kind is RetentionKind.SOURCE and not record.all_dependent_jobs_terminal:
            return False
        duration = {
            RetentionKind.TEMPORARY: self.temporary_for,
            RetentionKind.SOURCE: self.source_for,
            RetentionKind.ARTIFACT: self.artifact_for,
        }[record.kind]
        return now >= record.terminal_at + duration


class InMemoryRetentionRepository:
    """Fake repository whose delete operation models idempotent object deletion."""

    def __init__(self) -> None:
        self._records: dict[str, RetentionRecord] = {}
        self.deleted: set[str] = set()

    def add_temporary(self, identifier: str, *, terminal_at: datetime) -> None:
        self._add(RetentionRecord(identifier, RetentionKind.TEMPORARY, terminal_at))

    def add_source(
        self, identifier: str, *, terminal_at: datetime, all_jobs_terminal: bool
    ) -> None:
        self._add(RetentionRecord(identifier, RetentionKind.SOURCE, terminal_at, all_jobs_terminal))

    def add_artifact(self, identifier: str, *, terminal_at: datetime) -> None:
        self._add(RetentionRecord(identifier, RetentionKind.ARTIFACT, terminal_at))

    def candidates(self) -> tuple[RetentionRecord, ...]:
        return tuple(self._records.values())

    def delete(self, record: RetentionRecord) -> None:
        self._records.pop(record.identifier, None)
        self.deleted.add(record.identifier)

    def _add(self, record: RetentionRecord) -> None:
        self._records[record.identifier] = record


class PostgresRetention:
    """Bounded, retryable deletion of expired sources and outputs."""

    def __init__(self, database: Any, objects: Any) -> None:
        self.database = database
        self.objects = objects

    def run(self) -> int:
        deleted = 0
        # Lock the asset during deletion; admission takes the same row lock.
        with self.database.transaction():
            sources = self.database.execute("""
                SELECT a.id FROM assets a
                WHERE a.deleted_at IS NULL
                AND a.created_at < now() - interval '24 hours'
                AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.spec_json::jsonb->>'source_id'=a.id
                    AND (j.terminal_at IS NULL OR j.terminal_at > now() - interval '24 hours'))
                ORDER BY a.created_at LIMIT 100 FOR UPDATE OF a SKIP LOCKED
            """).fetchall()
            for source in sources:
                self.objects.delete_source(source["id"])
                self.database.execute(
                    "UPDATE assets SET deleted_at=now() WHERE id=%s", (source["id"],)
                )
                deleted += 1
        with self.database.transaction():
            artifacts = self.database.execute("""
                SELECT a.id, a.path FROM artifacts a JOIN jobs j ON j.id=a.job_id
                WHERE j.terminal_at < now() - interval '30 days'
                ORDER BY j.terminal_at LIMIT 100 FOR UPDATE OF a SKIP LOCKED
            """).fetchall()
            for artifact in artifacts:
                self.objects.delete_artifact(artifact["path"])
                self.database.execute("DELETE FROM artifacts WHERE id=%s", (artifact["id"],))
                deleted += 1
        with self.database.transaction():
            abandoned = self.database.execute("""
                SELECT id, resource_id FROM stored_objects o
                WHERE o.object_kind='temporary' AND o.deleted_at IS NULL
                AND o.terminal_at < now() - interval '24 hours'
                AND NOT EXISTS(SELECT 1 FROM artifacts a WHERE a.path=o.resource_id)
                ORDER BY o.terminal_at LIMIT 100 FOR UPDATE SKIP LOCKED
            """).fetchall()
            for item in abandoned:
                self.objects.delete_artifact(item["resource_id"])
                self.database.execute(
                    "UPDATE stored_objects SET deleted_at=now() WHERE id=%s", (item["id"],)
                )
                deleted += 1
        with self.database.transaction():
            checkpoints = self.database.execute("""
                SELECT c.id FROM job_checkpoints c JOIN jobs j ON j.id=c.job_id
                WHERE (NOT c.ready AND c.created_at < now() - interval '24 hours')
                   OR (j.status='succeeded' AND j.terminal_at < now() - interval '24 hours')
                   OR (j.status IN ('failed','cancelled')
                       AND j.terminal_at < now() - interval '7 days')
                ORDER BY c.created_at LIMIT 100 FOR UPDATE OF c SKIP LOCKED
            """).fetchall()
            for checkpoint in checkpoints:
                self.objects.delete_checkpoint(checkpoint["id"])
                self.database.execute(
                    "DELETE FROM job_checkpoints WHERE id=%s", (checkpoint["id"],)
                )
                deleted += 1
        return deleted
