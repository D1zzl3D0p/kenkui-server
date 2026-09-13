from __future__ import annotations

from kenkui_server.billing.models import AuthorizationStatus, InMemoryBillingRepository
from kenkui_server.billing.service import BillingService


def test_credit_pricing_is_flat_for_short_and_long_books() -> None:
    service = BillingService(InMemoryBillingRepository())

    assert service.credits_for_text("  hello\nworld  ") == 1000
    assert service.credits_for_text("x" * 1001) == 1000
    from kenkui_server.billing.pricing import credits_for_characters

    assert credits_for_characters(1_189_736) == 1000
    assert credits_for_characters(10_000_000) == 1000
    assert service.credits_for_text(" \n ") == 0


def test_duplicate_completed_attempt_settles_one_authorization() -> None:
    repository = InMemoryBillingRepository()
    service = BillingService(repository)
    service.grant_credits("account-1", 12, reference="purchase-1")
    authorization = service.reserve("job-1", "account-1", credits=12)

    service.finalize_success("job-1")
    service.finalize_success("job-1")

    assert repository.get_authorization(authorization.id).status is AuthorizationStatus.SETTLED
    assert [entry.kind for entry in repository.ledger_for_authorization(authorization.id)] == [
        "reservation",
        "settlement",
    ]
    assert repository.account("account-1").available_credits == 0


def test_failed_or_cancelled_job_releases_reserved_credits_once() -> None:
    repository = InMemoryBillingRepository()
    service = BillingService(repository)
    service.grant_credits("account-1", 3, reference="purchase-1")
    authorization = service.reserve("job-1", "account-1", credits=3)

    service.release("job-1")
    service.release("job-1")

    assert repository.get_authorization(authorization.id).status is AuthorizationStatus.RELEASED
    assert repository.account("account-1").available_credits == 3
    assert [entry.kind for entry in repository.ledger_for_authorization(authorization.id)] == [
        "reservation",
        "release",
    ]
