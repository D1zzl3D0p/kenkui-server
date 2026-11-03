"""Python oracle and actual fixture worker for disposable PG17 billing integration tests."""

import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

from kenkui_server.compute.hosted import HostedJobRunner, HostedWorkerStore
from kenkui_server.jobs.models import (
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.storage.assets import FakeS3Client, R2AssetStore
from kenkui_server.storage.postgres import PostgresHostedRepository
from kenkui_server.storage.postgres_database import PostgresDatabase

url = "postgresql://postgres@127.0.0.1:55438/postgres"
assert os.environ["KENKUI_TEST_DATABASE_URL"] == url
schema, operation, *args = sys.argv[1:]
assert re.fullmatch(r"billing_test_[a-f0-9]{32}", schema)
db = PostgresDatabase(url, schema=schema)
repo = PostgresHostedRepository(db)
source, owner, account = [f"00000000-0000-0000-0000-{i:012d}" for i in (1, 2, 3)]


def seed():
    db.execute("INSERT INTO identities(id) VALUES (%s)", (owner,))
    db.execute(
        "INSERT INTO credit_accounts(id,identity_id,available_credits) VALUES (%s,%s,10)",
        (account, owner),
    )
    db.execute(
        "INSERT INTO assets(id,owner_id,path,sha256,format) VALUES (%s,%s,'src','digest','epub')",
        (source, owner),
    )
    db.execute(
        "INSERT INTO credit_ledger_entries(id,account_id,kind,credits,reference) "
        "VALUES (gen_random_uuid(),%s,'purchase',1000,%s)",
        (account, "beta-allowance:" + owner),
    )
    for kind, ref, amount, spent in [
        ("purchase", "old", 5, 0),
        ("grant", "empty", 2, 2),
        ("grant", "beta-allowance:" + owner, 1000, 998),
        ("legacy", "legacy", 3, 0),
    ]:
        db.execute(
            "INSERT INTO credit_lots(id,account_id,kind,reference,credited,consumed) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (
                "new" if ref.startswith("beta-allowance:") else ref,
                account,
                kind,
                ref,
                amount,
                spent,
            ),
        )


def snapshot():
    tables = [
        "credit_accounts",
        "credit_lots",
        "credit_authorizations",
        "credit_allocations",
        "credit_ledger_entries",
        "payment_events",
        "jobs",
        "dispatches",
        "job_idempotency",
        "job_events",
    ]
    rows = {t: db.execute("SELECT * FROM " + t).fetchall() for t in tables}
    # Random UUIDs are validated and replaced by stable, distinct relational identities.
    aliases = {}
    for table, key in [
        ("credit_authorizations", "job_id"),
        ("credit_ledger_entries", "reference"),
        ("credit_lots", "reference"),
    ]:
        for row in rows[table]:
            if table == "credit_lots" and row["id"] in {"old", "new", "empty", "legacy"}:
                continue
            token = str(UUID(str(row["id"])))
            assert token not in aliases
            aliases[token] = table + "/" + row[key]

    def canonical(value):
        if isinstance(value, datetime):
            assert value.tzinfo is not None
            return "<db timestamp>"
        if isinstance(value, (str, UUID)):
            return aliases.get(str(value), str(value))
        return value

    return {
        t: sorted(
            [{k: canonical(v) for k, v in r.items()} for r in records],
            key=lambda x: json.dumps(x, sort_keys=True),
        )
        for t, records in rows.items()
    }


class BorrowedDatabase:
    def execute(self, *args):
        return db.execute(*args)

    def close(self):
        pass


try:
    if operation == "init":
        db.migrate()
        seed()
    elif operation == "reset":
        db.execute("TRUNCATE identities,payment_events RESTART IDENTITY CASCADE")
        seed()
    elif operation == "snapshot":
        print(json.dumps(snapshot(), sort_keys=True))
    elif operation == "allowance":
        from kenkui_server.hosted import create_hosted_app

        fake_env = {
            "WORKOS_REDIRECT_URI": "https://web.invalid/callback",
            "KENKUI_WEB_ORIGIN": "https://web.invalid",
            "KENKUI_SESSION_SECRET": base64.urlsafe_b64encode(bytes(32)).decode(),
            "WORKOS_API_KEY": "test",
            "WORKOS_CLIENT_ID": "test",
            "DATABASE_URL": url,
            "R2_BUCKET": "fixture",
            "R2_ENDPOINT": "https://r2.invalid",
            "R2_ACCESS_KEY_ID": "fixture",
            "R2_SECRET_ACCESS_KEY": "fixture",
        }
        with (
            patch.dict(os.environ, fake_env, clear=True),
            patch("kenkui_server.hosted.PostgresDatabase", return_value=db),
            patch("kenkui_server.hosted.object_store"),
            patch("workos.WorkOSClient"),
            patch("kenkui_server.hosted.select_hosted_voices", return_value=()),
            patch("kenkui_server.hosted.kk.list_voices"),
        ):
            services = create_hosted_app().state.hosted_services
            assert services.account_id_for_identity(UUID(args[0])) == args[1]
    elif operation == "admit":
        spec = JobSpec(
            source,
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("artifact.m4b"),
        )
        repo.admit(
            Job(args[0], spec),
            Dispatch(args[1], args[0], "pending"),
            account_id=account,
            owner_id=owner,
            credits=7,
            idempotency_key=hashlib.sha256(f"{owner}:full".encode()).hexdigest(),
        )
    elif operation == "payment":
        repo.billing.process_payment_event("stripe", "cs_full", account, 1100)
    elif operation == "release":
        assert repo.request_cancellation(args[0]).status.value == "cancelled"
    elif operation == "settle":
        dispatch_id = db.execute(
            "SELECT id FROM dispatches WHERE job_id=%s", (args[0],)
        ).fetchone()["id"]
        token = repo.claim_execution(dispatch_id, limit=1)
        if token:
            objects = R2AssetStore(FakeS3Client(), bucket="fixture")
            with TemporaryDirectory() as tmp:
                runner = HostedJobRunner(
                    "unused", objects, Path(tmp), lease=(dispatch_id, token), fixture_mode=True
                )
                runner._database = BorrowedDatabase
                runner._run(repo, HostedWorkerStore(objects, Path(tmp)), dispatch_id)
            assert repo.claim_execution(dispatch_id, limit=1) is None
        assert repo.jobs.get(args[0]).status.value == "succeeded"
    else:
        raise ValueError("unknown operation")
finally:
    db.close()
