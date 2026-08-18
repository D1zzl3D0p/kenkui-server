"""PostgreSQL row-mapping repositories for hosted durable state.

The adapter accepts a DB-API-compatible connection so deployment chooses its
PostgreSQL driver without leaking that choice into core job semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from kenkui_server.jobs.models import Job, JobStatus
from kenkui_server.storage.repositories import _decode_progress, _decode_spec


class PostgresCursor(Protocol):
    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> list[Mapping[str, Any]]: ...


class PostgresConnection(Protocol):
    def execute(self, statement: str, parameters: tuple[Any, ...]) -> PostgresCursor: ...


def _job_from_row(row: Mapping[str, Any]) -> Job:
    return Job(
        id=str(row["id"]),
        spec=_decode_spec(str(row["spec_json"])),
        status=JobStatus(str(row["status"])),
        version=int(row["version"]),
        progress=_decode_progress(str(row["progress_json"])),
    )


class PostgresJobRepository:
    """Maps PostgreSQL named rows to core job snapshots."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

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
