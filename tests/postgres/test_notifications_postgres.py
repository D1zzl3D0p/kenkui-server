"""Acceptance for notification storage against a disposable PostgreSQL schema."""

import os
from uuid import uuid4

import pytest

from kenkui_server.jobs.models import (
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.notifications.service import EMAIL_CHANNEL
from kenkui_server.storage.postgres import (
    PostgresHostedRepository,
    PostgresIdentityRepository,
    PostgresNotificationRepository,
)
from kenkui_server.storage.postgres_database import PostgresDatabase

pytestmark = pytest.mark.skipif(
    not os.environ.get("KENKUI_TEST_DATABASE_URL"),
    reason="requires a disposable PostgreSQL database",
)


@pytest.fixture
def database():
    url = os.environ["KENKUI_TEST_DATABASE_URL"]
    schema = "notify_test_" + uuid4().hex
    admin = PostgresDatabase(url)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    db = PostgresDatabase(url, schema=schema)
    try:
        db.migrate()
        yield db
    finally:
        db.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def admitted_job(database, owner):
    """A real job row, so the recipient join has something to resolve."""
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
    job = Job(str(uuid4()), spec)
    return repo.admit(
        job,
        Dispatch(str(uuid4()), job.id, "pending"),
        account_id=account,
        owner_id=str(owner),
        credits=2,
        idempotency_key=None,
    )


def test_a_new_identity_starts_notifiable_once_it_has_an_address(database):
    identities = PostgresIdentityRepository(database)
    owner = identities.user_id_for_subject("reader", "reader@example.com")

    assert identities.notification_settings(owner) == ("reader@example.com", True)


def test_sign_in_refreshes_the_address_but_never_erases_it(database):
    identities = PostgresIdentityRepository(database)
    owner = identities.user_id_for_subject("reader", "first@example.com")

    assert identities.user_id_for_subject("reader", "second@example.com") == owner
    assert identities.notification_settings(owner)[0] == "second@example.com"
    # A session carrying no address must not silently unsubscribe the reader.
    identities.user_id_for_subject("reader", None)
    assert identities.notification_settings(owner)[0] == "second@example.com"


def test_turning_the_preference_off_is_visible_to_the_worker(database):
    identities = PostgresIdentityRepository(database)
    owner = identities.user_id_for_subject("reader", "reader@example.com")
    job = admitted_job(database, owner)

    identities.set_notify_by_email(owner, False)

    recipient = PostgresNotificationRepository(database).recipient_for_job(job.id)
    assert recipient is not None
    assert (recipient.identity_id, recipient.email, recipient.notify_by_email) == (
        owner,
        "reader@example.com",
        False,
    )


def test_only_the_first_claim_may_send(database):
    identities = PostgresIdentityRepository(database)
    owner = identities.user_id_for_subject("reader", "reader@example.com")
    job = admitted_job(database, owner)
    notifications = PostgresNotificationRepository(database)

    assert notifications.claim(job.id, EMAIL_CHANNEL) is True
    assert notifications.claim(job.id, EMAIL_CHANNEL) is False

    notifications.mark_delivered(job.id, EMAIL_CHANNEL)
    row = database.execute(
        "SELECT delivered_at FROM job_notifications WHERE job_id = %s AND channel = %s",
        (job.id, EMAIL_CHANNEL),
    ).fetchone()
    assert row is not None and row["delivered_at"] is not None


def test_an_unknown_job_has_no_recipient(database):
    assert PostgresNotificationRepository(database).recipient_for_job("missing") is None
