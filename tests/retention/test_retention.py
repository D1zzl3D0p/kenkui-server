from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kenkui_server.storage.retention import InMemoryRetentionRepository, RetentionWorker


NOW = datetime(2026, 8, 18, tzinfo=UTC)


def test_retention_removes_expired_records_once_and_keeps_live_dependencies() -> None:
    repository = InMemoryRetentionRepository()
    repository.add_temporary("temp-old", terminal_at=NOW - timedelta(hours=24))
    repository.add_source("source-old", terminal_at=NOW - timedelta(hours=24), all_jobs_terminal=True)
    repository.add_artifact("artifact-old", terminal_at=NOW - timedelta(days=30))
    repository.add_source("source-live", terminal_at=NOW - timedelta(days=90), all_jobs_terminal=False)
    worker = RetentionWorker(repository)

    assert worker.run(NOW) == ("artifact-old", "source-old", "temp-old")
    assert worker.run(NOW) == ()
    assert repository.deleted == {"temp-old", "source-old", "artifact-old"}
    assert "source-live" not in repository.deleted
