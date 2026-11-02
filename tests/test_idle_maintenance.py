from datetime import UTC, datetime, timedelta

import pytest

from kenkui_server.maintenance import daily_maintenance, recover_with_signals
from kenkui_server.storage.recovery_signals import RecoverySignal


class Signals:
    def __init__(self, records=()):
        self.records = {record.job_id: record for record in records}

    def arm(self, job_id):
        self.records[job_id] = RecoverySignal(job_id, datetime.now(UTC))

    def clear(self, job_id):
        self.records.pop(job_id, None)

    def list_job_ids(self):
        return tuple(self.records)

    def list_records(self):
        return tuple(self.records.values())


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Database:
    def __init__(self, statuses):
        self.statuses = statuses
        self.closed = False
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if "NOT IN" in sql:
            return Cursor(
                [
                    {"id": key}
                    for key, value in self.statuses.items()
                    if value not in {"succeeded", "failed", "cancelled"}
                ]
            )
        return Cursor([{"status": self.statuses[params[0]]}] if params[0] in self.statuses else [])

    def close(self):
        self.closed = True


def test_empty_signals_never_open_a_pool():
    def forbidden(*args):
        pytest.fail("an empty minute tick must not create a pool")

    recover_with_signals(Signals(), forbidden, forbidden)


def test_minute_clears_only_terminal_job_and_preserves_active_and_missing():
    now = datetime.now(UTC)
    signals = Signals(
        [RecoverySignal(key, now - timedelta(days=2)) for key in ["done", "active", "missing"]]
    )
    database = Database({"done": "succeeded", "active": "running"})
    recover_with_signals(signals, lambda: database, lambda db: None)
    assert set(signals.list_job_ids()) == {"active", "missing"}
    assert database.closed
    assert all(sql.lstrip().startswith("SELECT") for sql in database.statements)


def test_recovery_failure_keeps_markers_and_closes_database():
    signals = Signals([RecoverySignal("active", datetime.now(UTC))])
    database = Database({"active": "running"})

    def fail(db):
        raise RuntimeError("recovery failed")

    with pytest.raises(RuntimeError, match="recovery failed"):
        recover_with_signals(signals, lambda: database, fail)
    assert signals.list_job_ids() == ("active",)
    assert database.closed


def test_daily_rearms_lost_active_marker_before_recovery_and_prunes_only_old_missing():
    now = datetime(2026, 11, 2, tzinfo=UTC)
    signals = Signals(
        [
            RecoverySignal("old-missing", now - timedelta(hours=25)),
            RecoverySignal("new-missing", now - timedelta(hours=23)),
            RecoverySignal("boundary", now - timedelta(hours=24)),
            RecoverySignal("done", now - timedelta(days=2)),
        ]
    )
    database = Database({"lost-active": "running", "done": "cancelled"})
    order = []

    def recover(db):
        assert "lost-active" in signals.list_job_ids()
        order.append("recover")

    daily_maintenance(
        signals,
        database,
        recover,
        lambda: order.append("retention"),
        lambda: order.append("backup"),
        now=now,
    )
    assert set(signals.list_job_ids()) == {"lost-active", "new-missing", "boundary"}
    assert order == ["recover", "retention", "backup"]
    assert all(sql.lstrip().startswith("SELECT") for sql in database.statements)


def test_backup_uploads_compressed_dump_before_pruning_to_seven_days(monkeypatch):
    from pathlib import Path

    from kenkui_server.maintenance import write_logical_backup

    events = []

    def dump(command, **kwargs):
        assert command[0] == "pg_dump"
        assert "--format=custom" in command and "--compress=6" in command
        assert not any("password" in arg for arg in command)
        assert kwargs["env"]["PGPASSWORD"] == "password"
        assert kwargs["env"]["PGSSLMODE"] == "verify-full"
        assert kwargs["env"]["PGSSLROOTCERT"] == "/etc/ssl/certs/ca-certificates.crt"
        Path(command[command.index("--file") + 1]).write_bytes(b"compressed archive")
        events.append("dump")

    class Client:
        def upload_file(self, path, bucket, key):
            assert bucket == "private"
            assert key == "operations/backups/staging/2026-11-02.dump"
            assert Path(path).read_bytes() == b"compressed archive"
            events.append("upload")

        def get_paginator(self, operation):
            return self

        def paginate(self, **kwargs):
            assert kwargs == {"Bucket": "private", "Prefix": "operations/backups/staging/"}
            yield {"Contents": [{"Key": "operations/backups/staging/2026-10-01.dump"}]}
            yield {
                "Contents": [
                    {"Key": f"operations/backups/staging/2026-11-{day:02}.dump"}
                    for day in range(1, 10)
                ]
            }

        def delete_object(self, *, Bucket, Key):
            events.append(Key)

    monkeypatch.setattr("kenkui_server.maintenance.subprocess.run", dump)
    write_logical_backup(
        Client(),
        bucket="private",
        environment="staging",
        database_url="postgresql://owner:password@db.example/db?sslmode=require",
        now=datetime(2026, 11, 2, tzinfo=UTC),
    )
    assert events[:2] == ["dump", "upload"]
    assert set(events[2:]) == {
        "operations/backups/staging/2026-10-01.dump",
        "operations/backups/staging/2026-11-01.dump",
        "operations/backups/staging/2026-11-02.dump",
    }


def test_failed_dump_never_uploads_or_prunes_and_hides_subprocess_details(monkeypatch):
    import subprocess

    from kenkui_server.maintenance import write_logical_backup

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["secret"], stderr=b"password")

    monkeypatch.setattr("kenkui_server.maintenance.subprocess.run", fail)
    with pytest.raises(RuntimeError, match="logical backup failed") as raised:
        write_logical_backup(
            object(),
            bucket="private",
            environment="staging",
            database_url="postgresql://owner:password@db.example/db",
            now=datetime.now(UTC),
        )
    assert "password" not in str(raised.value)
    assert raised.value.__suppress_context__


def test_backup_rejects_pooled_endpoint_before_running_dump():
    from kenkui_server.maintenance import write_logical_backup

    with pytest.raises(ValueError, match="unpooled"):
        write_logical_backup(
            object(),
            bucket="private",
            environment="staging",
            database_url="postgresql://owner:password@db-pooler.example/db",
            now=datetime.now(UTC),
        )


def test_hosted_idle_recovery_skips_database_entirely(monkeypatch):
    from kenkui_server import hosted

    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1")
    monkeypatch.setattr(hosted, "recovery_signals", lambda: Signals())
    monkeypatch.setattr(
        hosted,
        "PostgresDatabase",
        lambda *args, **kwargs: pytest.fail("empty hosted recovery opened Postgres"),
    )
    hosted.recover_jobs()


def test_invalid_backup_url_error_hides_credentials():
    from kenkui_server.maintenance import write_logical_backup

    with pytest.raises(ValueError, match="invalid logical backup connection") as raised:
        write_logical_backup(
            object(),
            bucket="private",
            environment="staging",
            database_url="password-secret",
            now=datetime.now(UTC),
        )
    assert "password-secret" not in str(raised.value)
    assert raised.value.__suppress_context__
