"""Real PostgreSQL fencing, publication ordering, isolation and retention."""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from test_real_postgres import database as database  # noqa: F401

from kenkui_server.storage.assets import FakeS3Client, R2AssetStore
from kenkui_server.storage.checkpoints import HostedCheckpointStore
from kenkui_server.storage.repositories import StaleWriteError
from kenkui_server.storage.retention import PostgresRetention

pytestmark = pytest.mark.skipif(
    not os.environ.get("KENKUI_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL"
)


def binding(database, job="job"):
    owner = str(uuid4())
    database.execute("INSERT INTO identities (id,workos_subject) VALUES (%s,%s)", (owner, job))
    database.execute(
        "INSERT INTO jobs (id,spec_json,status,version,progress_json,owner_id) "
        "VALUES (%s,'{}','running',1,'{}',%s)",
        (job, owner),
    )
    database.execute("INSERT INTO dispatches VALUES (%s,%s,'running',1)", (job, job))
    database.execute(
        "INSERT INTO execution_leases VALUES (%s,'token',%s,1)", (job, time.time() + 600)
    )
    objects = R2AssetStore(FakeS3Client(), bucket="private")
    return HostedCheckpointStore(database, objects, job, (job, "token"))


def test_roundtrip_replacement_and_job_isolation(database, tmp_path):
    store = binding(database)
    source = tmp_path / "source"
    source.write_bytes(b"chapter PCM")
    store.save("chapter", source, {"frames": 1})
    other = binding(database, "other")
    other.objects = store.objects
    assert other.restore("chapter", tmp_path / "other") is None
    assert store.restore("chapter", tmp_path / "restored") == {"frames": 1}
    assert (tmp_path / "restored").read_bytes() == b"chapter PCM"
    source.write_bytes(b"replacement")
    store.save("chapter", source, {"frames": 2})
    assert store.restore("chapter", tmp_path / "restored") == {"frames": 2}
    assert (tmp_path / "restored").read_bytes() == b"replacement"
    assert (
        database.execute("SELECT count(*) AS n FROM job_checkpoints WHERE ready").fetchone()["n"]
        == 1
    )


def test_failed_upload_never_publishes_and_preserves_previous(database, tmp_path, monkeypatch):
    store = binding(database)
    source = tmp_path / "source"
    source.write_bytes(b"previous")
    store.save("chapter", source, {})

    def fail(*args):
        raise RuntimeError("upload interrupted")

    monkeypatch.setattr(store.objects, "upload_checkpoint", fail)
    source.write_bytes(b"incomplete")
    with pytest.raises(RuntimeError, match="upload interrupted"):
        store.save("chapter", source, {})
    store.restore("chapter", tmp_path / "restored")
    assert (tmp_path / "restored").read_bytes() == b"previous"
    assert (
        database.execute("SELECT count(*) AS n FROM job_checkpoints WHERE NOT ready").fetchone()[
            "n"
        ]
        == 1
    )


def test_replaced_lease_cannot_publish_after_upload(database, tmp_path, monkeypatch):
    store = binding(database)
    source = tmp_path / "source"
    source.write_bytes(b"stale")
    original = store.objects.upload_checkpoint

    def replace_lease(identifier: str, path: Path):
        original(identifier, path)
        database.execute("UPDATE execution_leases SET token='replacement'")

    monkeypatch.setattr(store.objects, "upload_checkpoint", replace_lease)
    with pytest.raises(StaleWriteError):
        store.save("chapter", source, {})
    assert (
        database.execute("SELECT count(*) AS n FROM job_checkpoints WHERE ready").fetchone()["n"]
        == 0
    )


def test_corrupt_object_is_rejected(database, tmp_path):
    store = binding(database)
    source = tmp_path / "source"
    source.write_bytes(b"verified")
    store.save("chapter", source, {})
    row = database.execute("SELECT id FROM job_checkpoints").fetchone()
    source.write_bytes(b"corrupt!")
    store.objects.upload_checkpoint(row["id"], source)
    with pytest.raises(RuntimeError, match="checkpoint_integrity_failed"):
        store.restore("chapter", tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_retention_keeps_active_ready_checkpoints_and_cleans_terminal_jobs(database, tmp_path):
    store = binding(database)
    source = tmp_path / "source"
    source.write_bytes(b"checkpoint")
    store.save("chapter", source, {})
    database.execute("UPDATE job_checkpoints SET created_at=now()-interval '10 days'")
    retention = PostgresRetention(database, store.objects)
    assert retention.run() == 0
    database.execute("UPDATE jobs SET status='failed',terminal_at=now()-interval '2 days'")
    assert retention.run() == 0
    database.execute("UPDATE jobs SET terminal_at=now()-interval '8 days'")
    assert retention.run() == 1
    assert retention.run() == 0


def test_cancelled_job_cannot_write_checkpoints(database, tmp_path):
    store = binding(database)
    source = tmp_path / "source"
    source.write_bytes(b"checkpoint")
    database.execute("UPDATE jobs SET status='cancel_requested'")
    with pytest.raises(StaleWriteError):
        store.save("chapter", source, {})


def test_retention_reclaims_abandoned_uploads_without_deleting_active_checkpoint(
    database, tmp_path
):
    store = binding(database)
    source = tmp_path / "source"
    source.write_bytes(b"checkpoint")
    store.save("chapter", source, {})
    store.save("chapter", source, {})
    database.execute("UPDATE job_checkpoints SET created_at=now()-interval '2 days'")
    retention = PostgresRetention(database, store.objects)
    assert retention.run() == 1
    assert store.restore("chapter", tmp_path / "restored") == {}
    database.execute("UPDATE jobs SET status='succeeded',terminal_at=now()-interval '25 hours'")
    assert retention.run() == 1
    assert retention.run() == 0
