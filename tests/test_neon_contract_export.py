"""Synthetic wire contracts consumed by the Neon port; no provider access."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

BASELINE = Path(__file__).parents[1] / "contracts/neon/baseline.json"


def assert_supported_migrations(observed, frozen):
    # Only these reviewed additive migrations may extend the immutable baseline.
    assert observed == frozen + [
        "0006_provider_identities.sql",
        "0007_source_upload_intents.sql",
        "0008_migration_recovery.sql",
        "0009_migration_fixture_quarantine.sql",
        "0010_migration_legacy_fixture_finance.sql",
    ]


def test_contract_migration_allowlist_rejects_unknown_missing_or_reordered_migrations():
    frozen = json.loads(BASELINE.read_text())["postgresContract"]["migrations"]
    approved = frozen + [
        "0006_provider_identities.sql",
        "0007_source_upload_intents.sql",
        "0008_migration_recovery.sql",
        "0009_migration_fixture_quarantine.sql",
        "0010_migration_legacy_fixture_finance.sql",
    ]
    assert_supported_migrations(approved, frozen)
    for observed in [approved + ["9999_unreviewed.sql"], approved[:-1], list(reversed(approved))]:
        with pytest.raises(AssertionError):
            assert_supported_migrations(observed, frozen)


@pytest.mark.skipif(
    not os.environ.get("KENKUI_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL"
)
def test_contract_export_freezes_postgres_admission_and_payment_invariants():
    # Catches duplicate payment credits, non-atomic overspending, replayed jobs,
    # double settlement/refund, lost ownership, or missing SQL migrations.
    exporter = globals().get("export_database_contract")
    assert callable(exporter), "missing synthetic PostgreSQL invariant exporter"
    observed = exporter(os.environ["KENKUI_TEST_DATABASE_URL"])
    frozen = json.loads(BASELINE.read_text())["postgresContract"]
    # Only reviewed additive extensions are normalized. Every previous invariant
    # remains exact, and unknown/missing/reordered migrations still fail.
    assert_supported_migrations(observed["migrations"], frozen["migrations"])
    assert {**observed, "migrations": frozen["migrations"]} == frozen
    assert observed["creditsAfterPaymentReplay"] == 500
    assert observed["creditsAfterIdempotentAdmission"] == 300
    assert observed["concurrentAdmissions"] == {"accepted": 1, "rejected": 1}
    assert observed["creditsAfterFinalizationReplay"] == 200
    assert observed["authorizationStates"] == ["released", "settled"]
    assert observed["ownershipPreserved"] is True


def export_database_contract(url):
    """Migrate a new schema, exercise real SQL, then drop only that test schema."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import UUID, uuid4

    from kenkui_server.billing.models import AuthorizationStatus
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

    schema = "neon_contract_" + uuid4().hex
    admin = PostgresDatabase(url)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    db = PostgresDatabase(url, schema=schema)
    try:
        db.migrate()
        db.migrate()
        owner = PostgresIdentityRepository(db).user_id_for_subject("synthetic-contract-owner")
        account = str(UUID(int=100))
        db.execute(
            "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,0)",
            (account, str(owner)),
        )
        repo = PostgresHostedRepository(db)
        for _ in range(2):
            repo.billing.process_payment_event("stripe", "cs_contract", account, 500)
        payment_balance = repo.billing.account(account).available_credits
        spec = JobSpec(
            "source",
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("out.m4b"),
        )

        start = Barrier(2, timeout=5)

        def admit(index, credits, key):
            start.wait()
            job = Job(str(UUID(int=200 + index)), spec)
            return repo.admit(
                job,
                Dispatch(str(UUID(int=300 + index)), job.id, "pending"),
                account_id=account,
                owner_id=str(owner),
                credits=credits,
                idempotency_key=key,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            replay = list(pool.map(lambda i: admit(i, 200, "synthetic-replay"), range(2)))
        assert replay[0].id == replay[1].id
        admission_balance = repo.billing.account(account).available_credits

        def competing(index):
            try:
                return admit(index, 300, f"synthetic-competing-{index}")
            except ValueError as error:
                assert str(error) == "insufficient_credits"
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            competing_jobs = list(pool.map(competing, (2, 3)))
        accepted = [job for job in competing_jobs if job is not None]
        assert repo.billing.account(account).available_credits == 0
        for _ in range(2):
            repo.billing.finalize(replay[0].id, AuthorizationStatus.RELEASED)
            repo.billing.finalize(accepted[0].id, AuthorizationStatus.SETTLED)
        ledger = db.execute(
            "SELECT kind, count(*) AS count, sum(credits) AS credits FROM credit_ledger_entries "
            "GROUP BY kind ORDER BY kind"
        ).fetchall()
        return {
            "creditsAfterPaymentReplay": payment_balance,
            "creditsAfterIdempotentAdmission": admission_balance,
            "concurrentAdmissions": {"accepted": len(accepted), "rejected": 2 - len(accepted)},
            "creditsAfterFinalizationReplay": repo.billing.account(account).available_credits,
            "authorizationStates": sorted(
                row["status"]
                for row in db.execute("SELECT status FROM credit_authorizations").fetchall()
            ),
            "ownershipPreserved": all(
                repo.jobs.owner_id(job.id) == owner for job in (replay[0], accepted[0])
            ),
            "jobCount": db.execute("SELECT count(*) AS count FROM jobs").fetchone()["count"],
            "dispatchCount": db.execute("SELECT count(*) AS count FROM dispatches").fetchone()[
                "count"
            ],
            "ledger": [
                {"kind": r["kind"], "count": r["count"], "credits": int(r["credits"])}
                for r in ledger
            ],
            "migrations": [
                row["name"]
                for row in db.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
            ],
        }
    finally:
        db.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()
