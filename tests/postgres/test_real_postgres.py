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
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,10)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
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
    from kenkui_server.storage.assets import FakeS3Client, R2AssetStore
    from kenkui_server.jobs.models import Asset

    owner = PostgresIdentityRepository(database).user_id_for_subject("worker-user")
    account = str(uuid4())
    database.execute(
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,10)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
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
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,10)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
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
    from kenkui_server.storage.assets import R2AssetStore, FakeS3Client
    from kenkui_server.storage.retention import PostgresRetention
    from kenkui_server.jobs.models import Asset

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
        "INSERT INTO credit_accounts (id, identity_id, available_credits) VALUES (%s,%s,1000)",
        (account, str(owner)),
    )
    repo = PostgresHostedRepository(database)
    job = Job(str(uuid4()), JobSpec(
        "source", ("chapter",), SingleVoiceCasting("narrator"), TtsSettings(), OutputSpec("out.m4b")
    ))
    dispatch = Dispatch(str(uuid4()), job.id, "pending")
    repo.admit(job, dispatch, account_id=account, owner_id=str(owner),
               credits=1000, idempotency_key="orphan")
    token = repo.claim_execution(dispatch.id, limit=1)
    repo.update_job_and_append_event(
        transition(job, DispatchRequested()), expected_version=0, event_type="running",
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
