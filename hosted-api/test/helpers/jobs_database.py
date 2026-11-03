"""Existing Python lifecycle oracle; synthetic disposable local PostgreSQL only."""

import json
import os
import re
import sys

from kenkui_server.api.jobs import _response
from kenkui_server.storage.postgres import PostgresHostedRepository, PostgresNotificationRepository
from kenkui_server.storage.postgres_database import PostgresDatabase

schema, operation, *args = sys.argv[1:]
url = "postgresql://postgres@127.0.0.1:55438/postgres"
assert os.environ["KENKUI_TEST_DATABASE_URL"] == url
assert re.fullmatch(r"billing_test_[a-f0-9]{32}", schema)
db = PostgresDatabase(url + "?application_name=" + schema + "_python", schema=schema)
repo = PostgresHostedRepository(db)
try:
    if operation == "response":
        print(_response(repo.jobs.get(args[0])).model_dump_json(by_alias=True, exclude_none=True))
    elif operation == "recover":
        import subprocess
        from datetime import UTC, datetime
        from pathlib import Path

        from kenkui_server.jobs.dispatcher import HostedDispatcher
        from kenkui_server.maintenance import recover_with_signals
        from kenkui_server.storage.recovery_signals import R2RecoverySignals

        markers = json.loads(args[0])
        assert all(marker["Bucket"] == "fixture" and marker["Body"] == [] for marker in markers)
        keys = {marker["Key"] for marker in markers}
        opened = []
        launched = []

        class Client:
            def get_paginator(self, operation):
                assert operation == "list_objects_v2"
                return self

            def paginate(self, *, Bucket, Prefix):
                assert Bucket == "fixture"
                yield {
                    "Contents": [
                        {"Key": key, "LastModified": datetime.now(UTC)}
                        for key in keys
                        if key.startswith(Prefix)
                    ]
                }

            def delete_object(self, *, Bucket, Key):
                assert Bucket == "fixture"
                keys.remove(Key)

        signals = R2RecoverySignals(Client(), bucket="fixture", environment="staging")

        class Runner:
            def start(self, dispatch_id):
                launched.append(dispatch_id)
                job_id = repo.dispatches.get(dispatch_id).job_id
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("billing_database.py")),
                        schema,
                        "settle",
                        job_id,
                    ],
                    check=True,
                    capture_output=True,
                )

        class BorrowedDatabase:
            def execute(self, *args):
                return db.execute(*args)

            def close(self):
                pass

        def open_database():
            opened.append(True)
            return BorrowedDatabase()

        for _ in range(2):
            recover_with_signals(
                signals, open_database, lambda _: HostedDispatcher(repo, Runner()).recover()
            )
        print(json.dumps({"keys": sorted(keys), "opened": len(opened), "launched": len(launched)}))
    elif operation == "claim":
        print(json.dumps(repo.claim_execution(args[0], limit=1)))
    elif operation == "preferences":
        notifications = PostgresNotificationRepository(db)
        recipient = notifications.recipient_for_job(args[0])
        print(
            f"{recipient.email}|{recipient.notify_by_email}|"
            f"{notifications.claim(args[0], 'email')}|{notifications.claim(args[0], 'email')}"
        )
    elif operation == "complete":
        from dataclasses import replace

        from kenkui_server.jobs.models import Artifact, JobStatus, Progress

        current = repo.jobs.get(args[0])
        status = (
            JobStatus.CANCELLED
            if current.status is JobStatus.CANCEL_REQUESTED
            else JobStatus.SUCCEEDED
        )
        completed = replace(
            current, status=status, version=current.version + 1, progress=Progress("complete", 1, 1)
        )
        from kenkui_server.storage.repositories import StaleWriteError

        try:
            repo.update_job_and_append_event(
                completed,
                expected_version=current.version,
                event_type=status.value,
                artifact=Artifact(current.id, current.id, current.id, "m4b")
                if status is JobStatus.SUCCEEDED
                else None,
            )
            print(repo.jobs.get(current.id).status.value)
        except StaleWriteError:
            print("stale_write")
    else:
        raise ValueError("unknown oracle operation")
finally:
    db.close()
