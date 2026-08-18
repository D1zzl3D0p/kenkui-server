from pathlib import Path

import pytest

from kenkui_server.jobs.models import (
    Artifact,
    Asset,
    Dispatch,
    Inspection,
    InspectedChapter,
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
from kenkui_server.storage.repositories import Repositories, StaleWriteError


def job() -> Job:
    return Job(
        id="job-1",
        spec=JobSpec(
            source_id="asset-1",
            chapters=("chapter-a",),
            casting=SingleVoiceCasting(voice_id="en_US-amy"),
            tts=TtsSettings(),
            output=OutputSpec(path="/tmp/book.m4b"),
        ),
    )


def test_wal_database_round_trips_durable_local_state_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "server.sqlite3"
    database = Database(path)
    repositories = Repositories(database)
    repositories.assets.put(Asset("asset-1", "/tmp/book.epub", "abc123", "epub"))
    repositories.inspections.put(
        Inspection("asset-1", "Tiny", "Ada", (InspectedChapter("chapter-a", "One"),))
    )
    repositories.jobs.create(job())
    repositories.events.append(JobEvent("job-1", 1, "queued", Progress("queued", 0, 1)))
    repositories.dispatches.create(Dispatch("dispatch-1", "job-1", "pending"))
    repositories.artifacts.put(Artifact("artifact-1", "job-1", "/tmp/book.m4b", "m4b"))
    database.close()

    restarted = Repositories(Database(path))

    assert restarted.assets.get("asset-1").sha256 == "abc123"
    assert restarted.inspections.get("asset-1").chapters[0].id == "chapter-a"
    assert restarted.jobs.get("job-1") == job()
    assert restarted.events.list_for_job("job-1")[0].sequence == 1
    assert restarted.dispatches.get("dispatch-1").status == "pending"
    assert restarted.artifacts.list_for_job("job-1")[0].path == "/tmp/book.m4b"
    assert restarted.database.journal_mode() == "wal"
    restarted.database.close()


def test_snapshot_update_rejects_stale_concurrent_write(tmp_path: Path) -> None:
    repositories = Repositories(Database(tmp_path / "server.sqlite3"))
    repositories.jobs.create(job())
    running = Job(
        id="job-1",
        spec=job().spec,
        status=JobStatus.RUNNING,
        version=1,
        progress=Progress("synthesis", 0, 1),
    )

    repositories.jobs.update(running, expected_version=0)

    with pytest.raises(StaleWriteError, match="stale_write"):
        repositories.jobs.update(running, expected_version=0)


def test_event_sequence_is_unique_per_job(tmp_path: Path) -> None:
    repositories = Repositories(Database(tmp_path / "server.sqlite3"))
    repositories.jobs.create(job())
    event = JobEvent("job-1", 1, "queued", Progress("queued", 0, 1))
    repositories.events.append(event)

    with pytest.raises(ValueError, match="duplicate_event_sequence"):
        repositories.events.append(event)
