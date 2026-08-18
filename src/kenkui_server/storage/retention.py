"""Idempotent retention policy for hosted private storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol


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
