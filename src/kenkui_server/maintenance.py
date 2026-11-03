"""Signal-gated recovery and daily reconciliation of authoritative database state."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kenkui_server.jobs.models import JobStatus
from kenkui_server.storage.postgres_database import PostgresDatabase
from kenkui_server.storage.recovery_signals import (
    RecoverySignals,
    TimestampedRecoverySignals,
    operations_prefix,
)


def _status(database: PostgresDatabase, job_id: str) -> JobStatus | None:
    row = database.execute("SELECT status FROM jobs WHERE id = %s", (job_id,)).fetchone()
    return JobStatus(str(row["status"])) if row is not None else None


def _clear_terminal(
    signals: RecoverySignals, database: PostgresDatabase, job_ids: tuple[str, ...]
) -> None:
    for job_id in job_ids:
        status = _status(database, job_id)
        if status is not None and status.is_terminal:
            signals.clear(job_id)


def recover_with_signals(
    signals: RecoverySignals,
    open_database: Callable[[], PostgresDatabase],
    run_recovery: Callable[[PostgresDatabase], None],
) -> None:
    job_ids = signals.list_job_ids()
    if not job_ids:
        return
    database = open_database()
    try:
        run_recovery(database)
        _clear_terminal(signals, database, job_ids)
    finally:
        database.close()


def daily_maintenance(
    signals: TimestampedRecoverySignals,
    database: PostgresDatabase,
    run_recovery: Callable[[PostgresDatabase], None],
    run_retention: Callable[[], None],
    write_backup: Callable[[], None],
    *,
    now: datetime,
) -> None:
    # A marker is only a hint. Repair lost hints from authoritative job truth
    # before invoking the same lease-protected recovery as the minute tick.
    active = database.execute(
        "SELECT id FROM jobs WHERE status NOT IN ('succeeded', 'failed', 'cancelled')"
    ).fetchall()
    for row in active:
        signals.arm(str(row["id"]))
    run_recovery(database)
    for record in signals.list_records():
        status = _status(database, record.job_id)
        if (status is not None and status.is_terminal) or (
            status is None and record.modified_at < now - timedelta(hours=24)
        ):
            signals.clear(record.job_id)
    run_retention()
    write_backup()


def _backup_parameters(database_url: str) -> dict[str, str]:
    from psycopg import ProgrammingError
    from psycopg.conninfo import conninfo_to_dict

    try:
        parameters = {key: str(value) for key, value in conninfo_to_dict(database_url).items()}
    except ProgrammingError:
        raise ValueError("invalid logical backup connection") from None
    if not parameters.get("host") or "-pooler" in parameters["host"].lower():
        raise ValueError("logical backup requires an unpooled database host")
    allowed = {
        "host",
        "port",
        "dbname",
        "user",
        "password",
        "options",
        "channel_binding",
        "target_session_attrs",
        "application_name",
        "connect_timeout",
        "sslmode",
        "sslrootcert",
    }
    if parameters.keys() - allowed:
        raise ValueError("unsupported logical backup connection options")
    return parameters


def validate_backup_configuration(
    database_url: str, age_recipient: str, *, runtime_database_url: str | None = None
) -> None:
    """Validate direct backup authority and the offline public recipient before daily work."""
    import re

    from psycopg import ProgrammingError
    from psycopg.conninfo import conninfo_to_dict

    parameters = _backup_parameters(database_url)
    if runtime_database_url is not None:
        try:
            runtime = {
                key: str(value) for key, value in conninfo_to_dict(runtime_database_url).items()
            }
        except ProgrammingError:
            raise ValueError("invalid runtime database connection") from None
        if (
            not parameters.get("user")
            or not runtime.get("user")
            or parameters["user"] == runtime["user"]
        ):
            raise ValueError("logical backup requires a distinct database role")
        if (
            parameters["host"] != runtime.get("host", "").replace("-pooler", "")
            or not parameters.get("dbname")
            or parameters["dbname"] != runtime.get("dbname")
            or parameters.get("port", "5432") != runtime.get("port", "5432")
        ):
            raise ValueError("logical backup must target the runtime database")
    # Only native public X25519 recipients: no passphrase, private identity, SSH or plugins.
    if not re.fullmatch(r"age1[023456789acdefghjklmnpqrstuvwxyz]{58}", age_recipient):
        raise ValueError("invalid backup age recipient")
    try:
        # The vetted CLI verifies the recipient checksum and availability before any DB wake.
        subprocess.run(
            ["age", "--encrypt", "--recipient", age_recipient],
            input=b"",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "")},
            check=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        raise ValueError("invalid or unavailable backup age recipient") from None


def write_logical_backup(
    client: Any,
    *,
    bucket: str,
    environment: str,
    database_url: str,
    age_recipient: str,
    now: datetime,
) -> None:
    """Encrypt a full pg_dump to an offline recipient, then retain seven encrypted copies."""
    prefix = operations_prefix("backups", environment)
    validate_backup_configuration(database_url, age_recipient)
    parameters = _backup_parameters(database_url)
    # Keep credentials out of argv and errors. Use the image's system CA bundle
    # and hostname validation even if the application URL requests weaker TLS.
    pg_variables = {
        "host": "PGHOST",
        "port": "PGPORT",
        "dbname": "PGDATABASE",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "options": "PGOPTIONS",
        "channel_binding": "PGCHANNELBINDING",
        "target_session_attrs": "PGTARGETSESSIONATTRS",
        "application_name": "PGAPPNAME",
        "connect_timeout": "PGCONNECT_TIMEOUT",
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
    }
    if parameters.keys() - pg_variables.keys():
        raise ValueError("unsupported logical backup connection options")
    process_environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PG")
    }
    process_environment.update({pg_variables[key]: value for key, value in parameters.items()})
    process_environment.update(
        PGSSLMODE="verify-full",
        PGSSLROOTCERT="/etc/ssl/certs/ca-certificates.crt",
        PGCONNECT_TIMEOUT="30",
    )
    key = f"{prefix}{now.astimezone(UTC).date().isoformat()}.dump.age"
    with TemporaryDirectory(prefix="kenkui-backup-") as directory:
        archive = Path(directory) / "database.dump"
        try:
            subprocess.run(
                [
                    "pg_dump",
                    "--format=custom",
                    "--compress=6",
                    "--no-owner",
                    "--no-acl",
                    "--file",
                    str(archive),
                ],
                env=process_environment,
                check=True,
                capture_output=True,
                timeout=600,
            )
        except (subprocess.SubprocessError, OSError):
            raise RuntimeError("logical backup failed") from None
        encrypted = Path(directory) / "database.dump.age"
        try:
            subprocess.run(
                [
                    "age",
                    "--encrypt",
                    "--recipient",
                    age_recipient,
                    "--output",
                    str(encrypted),
                    str(archive),
                ],
                env={"PATH": os.environ.get("PATH", "")},
                check=True,
                capture_output=True,
                timeout=600,
            )
        except (subprocess.SubprocessError, OSError):
            raise RuntimeError("logical backup encryption failed") from None
        archive.unlink()
        client.upload_file(str(encrypted), bucket, key)
    backups = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            object_key = str(item["Key"])
            name = object_key.removeprefix(prefix)
            # Delete only date-named archives owned by this backup routine.
            if not object_key.startswith(prefix) or not name.endswith(".dump.age"):
                continue
            try:
                day = date.fromisoformat(name[:-9])
            except ValueError:
                continue
            if name == f"{day.isoformat()}.dump.age":
                backups.append(object_key)
    for expired in sorted(backups, reverse=True)[7:]:
        client.delete_object(Bucket=bucket, Key=expired)
