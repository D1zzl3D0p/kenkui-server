from __future__ import annotations

from kenkui_server.billing.models import AuthorizationStatus, InMemoryBillingRepository
from kenkui_server.billing.service import BillingService


def test_credit_pricing_scales_with_estimated_cost() -> None:
    service = BillingService(InMemoryBillingRepository())

    assert service.credits_for_text("  hello\nworld  ") == 1
    assert service.credits_for_text("x" * 1001) == 1
    from kenkui_server.billing.pricing import credits_for_characters

    assert credits_for_characters(1_189_736) == 300
    assert credits_for_characters(10_000_000) == 2520
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


def test_cost_calibration_and_multivoice_premium(monkeypatch):
    import pytest

    from kenkui_server.billing.pricing import credits_for_characters

    assert credits_for_characters(1_189_736, multivoice=True) == 450
    assert credits_for_characters(0, multivoice=True) == 0
    with pytest.raises(ValueError):
        credits_for_characters(-1)
    monkeypatch.setenv("KENKUI_ESTIMATED_COST_CENTS_PER_MILLION", "150")
    assert credits_for_characters(1_000_000) == 300
    assert credits_for_characters(1_000_000, multivoice=True) == 450
