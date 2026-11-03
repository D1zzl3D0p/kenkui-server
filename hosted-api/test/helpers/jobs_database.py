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
