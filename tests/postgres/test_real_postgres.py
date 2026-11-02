"""Acceptance against a disposable PostgreSQL schema, never operator tables."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

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

pytestmark = pytest.mark.skipif(
    not os.environ.get("KENKUI_TEST_DATABASE_URL"),
    reason="requires a disposable PostgreSQL database",
)


@pytest.fixture
def database():
    url = os.environ["KENKUI_TEST_DATABASE_URL"]
    schema = "beta_test_" + uuid4().hex
    admin = PostgresDatabase(url)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    db = PostgresDatabase(url, schema=schema)
    try:
        db.migrate()
        db.migrate()
        yield db
    finally:
        db.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_real_admission_replay_and_release(database):
    owner = PostgresIdentityRepository(database).user_id_for_subject("beta-user")
    account = str(uuid4())
    database.execute(
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,0)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
    repo.billing.grant(account, 10, reference="test-grant")
    spec = JobSpec(
        "source", ("chapter",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("out.m4b")
    )

    def admit(_):
        job = Job(str(uuid4()), spec)
        return repo.admit(
            job,
            Dispatch(str(uuid4()), job.id, "pending"),
            account_id=account,
            owner_id=str(owner),
            credits=2,
            idempotency_key="same-request",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(admit, range(2)))
    assert jobs[0].id == jobs[1].id
    assert repo.billing.account(account).available_credits == 8
    repo.billing.finalize(jobs[0].id, AuthorizationStatus.RELEASED)
    repo.billing.finalize(jobs[0].id, AuthorizationStatus.RELEASED)
    assert repo.billing.account(account).available_credits == 10
    entries = repo.billing.ledger_for_authorization(
        repo.billing.authorization_for_job(jobs[0].id).id
    )
    assert sorted(e.kind for e in entries) == ["release", "reservation"]


def test_hosted_worker_atomically_publishes_artifact_and_settles_credit(database, tmp_path):
    from kenkui_server.compute.hosted import HostedJobRunner, HostedWorkerStore
    from kenkui_server.jobs.models import Asset
    from kenkui_server.storage.assets import FakeS3Client, R2AssetStore

    owner = PostgresIdentityRepository(database).user_id_for_subject("worker-user")
    account = str(uuid4())
    database.execute(
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,0)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
    repo.billing.grant(account, 10, reference="test-grant")
    repo.assets.put_for_owner(Asset("source", "source", "digest", "epub"), owner)
    spec = JobSpec(
        "source", ("chapter",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("out.m4b")
    )
    job = Job(str(uuid4()), spec)
    dispatch = Dispatch(str(uuid4()), job.id, "pending")
    repo.admit(
        job,
        dispatch,
        account_id=account,
        owner_id=str(owner),
        credits=2,
        idempotency_key="worker-request",
    )
    token = repo.claim_execution(dispatch.id, limit=1)
    objects = R2AssetStore(FakeS3Client(), bucket="private")
    runner = HostedJobRunner(
        "unused", objects, tmp_path, lease=(dispatch.id, token), fixture_mode=True
    )

    class BorrowedDatabase:
        def execute(self, *args):
            return database.execute(*args)

        def close(self):
            pass

    runner._database = lambda: BorrowedDatabase()
    runner._run(repo, HostedWorkerStore(objects, tmp_path), dispatch.id)
    assert repo.jobs.get(job.id).status.value == "succeeded"
    (artifact,) = repo.artifacts.list_for_job(job.id)
    assert objects.read_artifact(artifact.path) == b"KENKUI-FIXTURE-M4B\n"
    assert repo.billing.authorization_for_job(job.id).status is AuthorizationStatus.SETTLED
    assert repo.billing.account(account).available_credits == 8
    assert repo.claim_execution(dispatch.id, limit=1) is None


def test_hosted_queued_cancellation_releases_allowance(database):
    owner = PostgresIdentityRepository(database).user_id_for_subject("cancel-user")
    account = str(uuid4())
    database.execute(
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,0)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
    repo.billing.grant(account, 10, reference="test-grant")
    job = Job(
        str(uuid4()),
        JobSpec(
            "source",
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("out.m4b"),
        ),
    )
    repo.admit(
        job,
        Dispatch(str(uuid4()), job.id, "pending"),
        account_id=account,
        owner_id=str(owner),
        credits=2,
        idempotency_key=None,
    )
    assert repo.request_cancellation(job.id).status.value == "cancelled"
    repo.request_cancellation(job.id)
    assert repo.billing.account(account).available_credits == 10


def test_retention_deletes_expired_unsubmitted_sources_once(database):
    from kenkui_server.jobs.models import Asset
    from kenkui_server.storage.assets import FakeS3Client, R2AssetStore
    from kenkui_server.storage.retention import PostgresRetention

    owner = PostgresIdentityRepository(database).user_id_for_subject("retention-user")
    repo = PostgresHostedRepository(database)
    repo.assets.put_for_owner(Asset("expired", "expired", "digest", "epub"), owner)
    database.execute("UPDATE assets SET created_at=now()-interval '2 days'")
    objects = R2AssetStore(FakeS3Client(), bucket="private")
    objects.put_source("expired", b"source")
    retention = PostgresRetention(database, objects)
    assert retention.run() == 1
    assert retention.run() == 0
    with pytest.raises(KeyError):
        objects.read_source("expired")


def test_legacy_orphan_recovery_preserves_reservation_and_refunds_on_exhaustion(database):
    from kenkui_server.jobs.transitions import DispatchRequested, transition
    from kenkui_server.storage.repositories import StaleWriteError

    owner = PostgresIdentityRepository(database).user_id_for_subject("orphan-user")
    account = str(uuid4())
    database.execute(
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,0)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
    repo.billing.grant(account, 1000, reference="test-grant")
    job = Job(
        str(uuid4()),
        JobSpec(
            "source",
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("out.m4b"),
        ),
    )
    dispatch = Dispatch(str(uuid4()), job.id, "pending")
    repo.admit(
        job,
        dispatch,
        account_id=account,
        owner_id=str(owner),
        credits=1000,
        idempotency_key="orphan",
    )
    token = repo.claim_execution(dispatch.id, limit=1)
    repo.update_job_and_append_event(
        transition(job, DispatchRequested()),
        expected_version=0,
        event_type="running",
        lease=(dispatch.id, token),
    )
    with pytest.raises(StaleWriteError):
        repo.finish_dispatch_if_not_cancellation_requested(repo.dispatches.get(dispatch.id))
    # Reproduce the corrupted state written by the pre-fix worker.
    database.execute("UPDATE dispatches SET status='done' WHERE id=%s", (dispatch.id,))
    repo.release_execution(dispatch.id, token)
    assert [d.id for d in repo.dispatches.list_incomplete()] == [dispatch.id]
    for _ in range(2):
        token = repo.claim_execution(dispatch.id, limit=1)
        assert token
        assert repo.dispatches.get(dispatch.id).status == "pending"
        assert repo.billing.account(account).available_credits == 0
        assert repo.billing.authorization_for_job(job.id).status is AuthorizationStatus.RESERVED
        repo.release_execution(dispatch.id, token)
    assert repo.claim_execution(dispatch.id, limit=1) is None
    assert repo.jobs.get(job.id).status.value == "failed"
    assert repo.billing.account(account).available_credits == 1000
    assert repo.dispatches.list_incomplete() == ()
    entries = repo.billing.ledger_for_authorization(repo.billing.authorization_for_job(job.id).id)
    assert sorted(e.kind for e in entries) == ["release", "reservation"]


def lot_account(database):
    owner = PostgresIdentityRepository(database).user_id_for_subject(str(uuid4()))
    account = str(uuid4())
    database.execute(
        "INSERT INTO credit_accounts (id,identity_id) VALUES (%s,%s)", (account, owner)
    )
    return account, owner, PostgresHostedRepository(database)


def reserve_lot_job(database, repo, account, owner, credits):
    job = Job(
        str(uuid4()),
        JobSpec(
            "source",
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("out.m4b"),
        ),
    )
    repo.admit(
        job,
        Dispatch(str(uuid4()), job.id, "pending"),
        account_id=account,
        owner_id=str(owner),
        credits=credits,
        idempotency_key=None,
    )
    return job.id


def test_pack_allocations_survive_release_and_settlement_in_reverse_order(database):
    account, owner, repo = lot_account(database)
    repo.billing.process_payment_event("stripe", "old", account, 1100)
    repo.billing.process_payment_event("stripe", "new", account, 2400)
    repo.billing.grant(account, 100, reference="complimentary")
    first = reserve_lot_job(database, repo, account, owner, 1150)
    second = reserve_lot_job(database, repo, account, owner, 100)
    lots = repo.billing.credit_lots(account)
    assert [lot.reserved for lot in lots] == [1100, 50, 100]
    repo.billing.finalize(second, AuthorizationStatus.SETTLED)
    repo.billing.finalize(first, AuthorizationStatus.RELEASED)
    repo.billing.finalize(first, AuthorizationStatus.RELEASED)
    lots = repo.billing.credit_lots(account)
    assert [lot.consumed for lot in lots] == [50, 50, 0]
    assert (
        sum(lot.available for lot in lots)
        == repo.billing.account(account).available_credits
        == 3500
    )
    assert all(lot.reserved == 0 for lot in lots)


def test_concurrent_pack_spending_and_webhook_replays(database):
    account, owner, repo = lot_account(database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: repo.billing.process_payment_event("stripe", "same", account, 500),
                range(2),
            )
        )
    assert len(repo.billing.credit_lots(account)) == 1

    def spend(_):
        try:
            return reserve_lot_job(database, repo, account, owner, 400)
        except ValueError as error:
            assert str(error) == "insufficient_credits"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(spend, range(2)))
    (job,) = [job for job in jobs if job]
    assert repo.billing.credit_lots(account)[0].reserved == 400
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: repo.billing.finalize(job, AuthorizationStatus.SETTLED), range(2)))
    (lot,) = repo.billing.credit_lots(account)
    assert (lot.available, lot.reserved, lot.consumed, lot.usage_status) == (100, 0, 400, "used")


def test_mismatched_pack_balance_rolls_back_entire_admission(database):
    account, owner, repo = lot_account(database)
    # An unsupported direct adjustment must fail closed, not silently spend
    # untracked funds and claim that all paid packs are unused.
    database.execute("UPDATE credit_accounts SET available_credits=50 WHERE id=%s", (account,))
    with pytest.raises(ValueError, match="credit_lot_balance_mismatch"):
        reserve_lot_job(database, repo, account, owner, 25)
    assert repo.billing.account(account).available_credits == 50
    assert database.execute("SELECT id FROM jobs").fetchall() == []
    assert database.execute("SELECT id FROM credit_authorizations").fetchall() == []


def test_migration_keeps_historical_balances_and_pending_allocations(database):
    # Recreate the pre-lot state within this disposable schema only.
    database.execute("DROP TABLE credit_allocations")
    database.execute("DROP TABLE credit_lots")
    database.execute(
        "ALTER TABLE credit_authorizations DROP CONSTRAINT credit_authorization_account_unique"
    )
    database.execute("DELETE FROM schema_migrations WHERE name='0004_credit_lots.sql'")
    account, owner, repo = lot_account(database)
    database.execute("UPDATE credit_accounts SET available_credits=80 WHERE id=%s", (account,))
    database.execute(
        "INSERT INTO credit_ledger_entries (id,account_id,kind,credits,reference) "
        "VALUES (%s,%s,'purchase',100,'stripe:historical')", (str(uuid4()), account)
    )
    database.execute(
        "INSERT INTO payment_events (provider,provider_event_id) VALUES ('stripe','historical')"
    )
    job = Job(
        str(uuid4()),
        JobSpec(
            "source",
            ("chapter",),
            SingleVoiceCasting("narrator"),
            TtsSettings(),
            OutputSpec("out.m4b"),
        ),
    )
    from kenkui_server.storage.repositories import _encode_progress, _encode_spec

    database.execute(
        "INSERT INTO jobs (id,spec_json,status,version,progress_json,owner_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (
            job.id,
            _encode_spec(job.spec),
            job.status.value,
            0,
            _encode_progress(job.progress),
            owner,
        ),
    )
    database.execute(
        "INSERT INTO credit_authorizations (id,job_id,account_id,credits,status) "
        "VALUES (%s,%s,%s,20,'reserved')",
        (str(uuid4()), job.id, account),
    )
    database.migrate()
    database.migrate()
    repo.billing.process_payment_event("stripe", "historical", account, 100)
    (lot,) = repo.billing.credit_lots(account)
    assert (lot.credited, lot.available, lot.reserved, lot.usage_status) == (
        100,
        80,
        20,
        "manual_review",
    )
    repo.billing.finalize(job.id, AuthorizationStatus.RELEASED)
    assert repo.billing.account(account).available_credits == 100
    assert repo.billing.credit_lots(account)[0].available == 100
    repo.billing.process_payment_event("stripe", "new", account, 500)
    new_job = reserve_lot_job(database, repo, account, owner, 90)
    repo.billing.finalize(new_job, AuthorizationStatus.SETTLED)
    lots = repo.billing.credit_lots(account)
    assert lots[0].consumed == 90
    assert lots[1].usage_status == "unused"


class RecoverySignals:
    def __init__(self):
        self.records = {}

    def arm(self, job_id):
        from datetime import UTC, datetime

        from kenkui_server.storage.recovery_signals import RecoverySignal

        self.records[job_id] = RecoverySignal(job_id, datetime.now(UTC))

    def clear(self, job_id):
        self.records.pop(job_id, None)

    def list_job_ids(self):
        return tuple(self.records)

    def list_records(self):
        return tuple(self.records.values())


def test_concurrent_signal_admission_replay_reserves_once(database):
    from kenkui_server.jobs.dispatcher import HostedDispatcher

    account, owner, repo = lot_account(database)
    repo.billing.grant(account, 10, reference="signal-grant")
    signals = RecoverySignals()
    started = []

    class Runner:
        def start(self, dispatch_id):
            started.append(dispatch_id)

    dispatcher = HostedDispatcher(repo, Runner(), recovery_signals=signals)
    spec = JobSpec(
        "source", ("chapter",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("out.m4b")
    )

    def submit(_):
        return dispatcher.submit(
            spec,
            owner_id=owner,
            account_id=account,
            normalized_speech_characters=1,
            idempotency_key="same",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(submit, range(2))
    assert first.id == second.id
    assert signals.list_job_ids() == (first.id,)
    assert repo.billing.account(account).available_credits == 9
    assert len(started) == 1
    authorization = repo.billing.authorization_for_job(first.id)
    assert [entry.kind for entry in repo.billing.ledger_for_authorization(authorization.id)] == [
        "reservation"
    ]


def test_daily_signal_reconciliation_preserves_leases_and_cannot_finalize_credits_twice(database):
    from datetime import UTC, datetime

    from kenkui_server.jobs.dispatcher import HostedDispatcher
    from kenkui_server.maintenance import daily_maintenance, recover_with_signals

    account, owner, repo = lot_account(database)
    repo.billing.grant(account, 10, reference="signal-grant")
    first = reserve_lot_job(database, repo, account, owner, 1)
    second = reserve_lot_job(database, repo, account, owner, 1)
    signals = RecoverySignals()
    signals.arm(first)  # second has lost its external signal
    repo.request_cancellation(first)
    assert repo.billing.account(account).available_credits == 9
    leases = []

    class Runner:
        def start(self, dispatch_id):
            token = repo.claim_execution(dispatch_id, limit=1)
            if token:
                leases.append((dispatch_id, token))

    def recover(db):
        assert second in signals.list_job_ids()
        HostedDispatcher(repo, Runner()).recover()

    for _ in range(2):
        daily_maintenance(
            signals, database, recover, lambda: None, lambda: None, now=datetime.now(UTC)
        )
    assert signals.list_job_ids() == (second,)
    assert len(leases) == 1  # repeated recovery cannot exceed the live database lease
    assert repo.billing.account(account).available_credits == 9
    repo.release_execution(*leases[0])
    repo.request_cancellation(second)

    class BorrowedDatabase:
        def execute(self, *args):
            return database.execute(*args)

        def close(self):
            pass

    recover_with_signals(
        signals, BorrowedDatabase, lambda db: HostedDispatcher(repo, Runner()).recover()
    )
    recover_with_signals(
        signals, lambda: pytest.fail("terminal marker was not cleared"), lambda db: None
    )
    assert signals.list_job_ids() == ()
    assert repo.billing.account(account).available_credits == 10
    for job_id in (first, second):
        authorization = repo.billing.authorization_for_job(job_id)
        assert sorted(
            entry.kind for entry in repo.billing.ledger_for_authorization(authorization.id)
        ) == ["release", "reservation"]
