from __future__ import annotations

import json

from kenkui_server.jobs.models import JobStatus
from kenkui_server.storage.postgres import PostgresJobRepository


class Cursor:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.statement = ""
        self.parameters: tuple[object, ...] = ()

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchone(self) -> dict[str, object]:
        return self.row


class Connection:
    def __init__(self, row: dict[str, object]) -> None:
        self.cursor = Cursor(row)

    def execute(self, statement: str, parameters: tuple[object, ...]) -> Cursor:
        self.cursor.execute(statement, parameters)
        return self.cursor


def test_postgres_repository_maps_named_rows_without_sqlite_placeholder_syntax() -> None:
    connection = Connection(
        {
            "id": "job-1",
            "spec_json": json.dumps(
                {
                    "source_id": "source-1",
                    "chapters": ["chapter-1"],
                    "casting": {"voice_id": "voice-1"},
                    "tts": {"normalize_text": True},
                    "output": {"path": "private.m4b"},
                }
            ),
            "status": "queued",
            "version": 0,
            "progress_json": json.dumps({"stage": "queued", "completed": 0, "total": 1}),
        }
    )

    job = PostgresJobRepository(connection).get("job-1")

    assert job.status is JobStatus.QUEUED
    assert connection.cursor.parameters == ("job-1",)
    assert "%s" in connection.cursor.statement
    assert "?" not in connection.cursor.statement
