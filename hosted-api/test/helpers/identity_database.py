"""Synthetic-only cross-language fixture. Never connects to a provider database."""

import json
import os
import sys
import tempfile
from pathlib import Path

from kenkui_server.jobs.models import (
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.storage.postgres import PostgresHostedRepository, PostgresIdentityRepository
from kenkui_server.storage.postgres_database import PostgresDatabase

URL = "postgresql://postgres@127.0.0.1:55438/postgres"
assert os.environ["KENKUI_TEST_DATABASE_URL"] == URL
schema, operation = sys.argv[1:]
assert schema.startswith("identity_test_") and schema.replace("_", "").isalnum()
db = PostgresDatabase(URL, schema=schema)
owner = "11111111-1111-4111-8111-111111111111"
account = "33333333-3333-4333-8333-333333333333"
try:
    if operation == "initialize":
        # Populate the old schema first, then apply the additive migration through
        # the sole production migration authority. Proves preservation on upgrade.
        root = Path(__file__).resolve().parents[3] / "migrations/postgresql"
        with tempfile.TemporaryDirectory(prefix="identity-old-migrations-") as temp:
            for migration in root.glob("*.sql"):
                if migration.name < "0006":
                    (Path(temp) / migration.name).write_text(migration.read_text())
            db.migrate(Path(temp))
        db.execute(
            "INSERT INTO identities (id, workos_subject, email) VALUES (%s,%s,%s)",
            (owner, "legacy-subject", "old@example.invalid"),
        )
        db.execute("INSERT INTO credit_accounts (id, identity_id) VALUES (%s,%s)", (account, owner))
        repo = PostgresHostedRepository(db)
        for _ in range(2):
            repo.billing.process_payment_event("stripe", "synthetic-payment", account, 20)
        spec = JobSpec(
            "source",
            ("章-é",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("book.m4b"),
        )
        job = Job("book-before", spec)
        repo.admit(
            job,
            Dispatch("dispatch-before", job.id, "pending"),
            account_id=account,
            owner_id=owner,
            credits=7,
            idempotency_key="before-link",
        )
        db.migrate()
        db.migrate()
    elif operation == "roundtrip":
        row = db.execute(
            "SELECT id, identity_id, available_credits FROM credit_accounts WHERE id=%s", (account,)
        ).fetchone()
        assert str(row["identity_id"]) == owner
        assert row["available_credits"] == 9007199254740993
        progress = db.execute(
            "SELECT progress_json FROM jobs WHERE id=%s", ("book-before",)
        ).fetchone()
        assert json.loads(progress["progress_json"]) == {
            "stage": "章-é",
            "completed": 9007199254740993,
            "total": 9223372036854775806,
        }
        job = PostgresHostedRepository(db).jobs.get("book-before")
        assert job.spec.chapters == ("章-é",)
        assert job.progress.completed == 9007199254740993
        assert job.progress.total == 9223372036854775806
        identities = PostgresIdentityRepository(db)
        assert str(identities.user_id_for_subject("legacy-subject")) == owner
        neon_owner = db.execute("SELECT id FROM identities WHERE workos_subject IS NULL").fetchone()
        assert neon_owner is not None
        assert identities.notification_settings(neon_owner["id"]) == (None, True)
        db.execute(
            "UPDATE credit_accounts SET available_credits=%s WHERE id=%s",
            (9223372036854775806, account),
        )
        # Existing JSON remains TEXT; keep exact integer values in serialized JSON.
        value = {"stage": "章-é", "completed": 9007199254740994, "total": 9223372036854775806}
        db.execute(
            "UPDATE jobs SET progress_json=%s WHERE id=%s",
            (json.dumps(value, ensure_ascii=False), "book-before"),
        )
        print(
            json.dumps(
                {
                    "owner": str(row["identity_id"]),
                    "neonOwner": str(neon_owner["id"]),
                    "credits": str(row["available_credits"]),
                }
            )
        )
    else:
        raise ValueError("unknown synthetic operation")
finally:
    db.close()
