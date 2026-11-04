"""Cloud worker services and explicit migration/recovery commands."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from kenkui_server.compute.modal import ModalProcessRunner
from kenkui_server.storage.assets import R2AssetStore
from kenkui_server.storage.postgres import PostgresHostedRepository
from kenkui_server.storage.postgres_database import PostgresDatabase
from kenkui_server.storage.recovery_signals import R2RecoverySignals


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing configuration: {name}")
    return value


def r2_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=required("R2_ENDPOINT"),
        aws_access_key_id=required("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=required("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
    )


def object_store() -> R2AssetStore:
    return R2AssetStore(r2_client(), bucket=required("R2_BUCKET"), key_salt=required("R2_KEY_SALT"))


def recovery_signals() -> R2RecoverySignals:
    return R2RecoverySignals(
        r2_client(), bucket=required("R2_BUCKET"), environment=required("KENKUI_DEPLOYMENT")
    )


def _recovery_database() -> PostgresDatabase:
    database = PostgresDatabase(
        required("DATABASE_URL"),
        idle=os.environ.get("KENKUI_DATABASE_IDLE") == "1",
        connect_timeout=15,
    )
    try:
        database.wait_until_ready(timeout=90)
    except BaseException:
        database.close()
        raise
    return database


def _run_recovery(database: PostgresDatabase) -> None:
    from kenkui_server.jobs.dispatcher import HostedDispatcher

    repo = PostgresHostedRepository(database)
    HostedDispatcher(
        repo,
        ModalProcessRunner(
            repo,
            app_name=os.environ.get("KENKUI_MODAL_APP", "kenkui-beta"),
            max_jobs=int(os.environ.get("KENKUI_MAX_JOBS", "2")),
        ),
    ).recover()


def recover_jobs() -> None:
    from kenkui_server.maintenance import recover_with_signals

    if os.environ.get("KENKUI_DATABASE_IDLE") == "1":
        recover_with_signals(recovery_signals(), _recovery_database, _run_recovery)
    else:
        database = _recovery_database()
        try:
            _run_recovery(database)
        finally:
            database.close()


def retain_objects() -> None:
    from kenkui_server.maintenance import (
        daily_maintenance,
        validate_backup_configuration,
        write_logical_backup,
    )
    from kenkui_server.storage.migration_challenges import prune_migration_recovery
    from kenkui_server.storage.retention import PostgresRetention

    idle_mode = os.environ.get("KENKUI_DATABASE_IDLE") == "1"
    if idle_mode:
        backup_url = required("DATABASE_URL_UNPOOLED")
        age_recipient = required("KENKUI_BACKUP_AGE_RECIPIENT")
        validate_backup_configuration(
            backup_url, age_recipient, runtime_database_url=required("DATABASE_URL")
        )
    database = _recovery_database()
    try:
        retention = PostgresRetention(database, object_store())

        def run_retention() -> None:
            retention.run()
            # One shared bounded batch in the daily idle path, never the minute tick.
            with database.connection() as connection:
                prune_migration_recovery(connection, batch_size=100)

        if idle_mode:
            now = datetime.now(UTC)
            daily_maintenance(
                recovery_signals(),
                database,
                _run_recovery,
                run_retention,
                lambda: write_logical_backup(
                    r2_client(),
                    bucket=required("R2_BUCKET"),
                    environment=required("KENKUI_DEPLOYMENT"),
                    database_url=backup_url,
                    age_recipient=age_recipient,
                    now=now,
                ),
                now=now,
            )
        else:
            retention.run()
    finally:
        database.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Worker recovery and explicit database migrations")
    parser.add_argument("command", choices=["migrate", "recover"])
    command = parser.parse_args().command
    if command == "migrate":
        database = PostgresDatabase(
            required("DATABASE_URL"), idle=os.environ.get("KENKUI_DATABASE_IDLE") == "1"
        )
        try:
            database.migrate()
        finally:
            database.close()
    else:
        recover_jobs()


if __name__ == "__main__":
    main()
