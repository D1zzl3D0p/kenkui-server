from __future__ import annotations

import pytest

from kenkui_server.billing.models import AuthorizationStatus, InMemoryBillingRepository
from kenkui_server.billing.service import BillingService, CreditAwareJobAdmission, JobBillingFinalizer
from kenkui_server.jobs.models import JobStatus


def test_admission_reserves_before_queue_and_releases_when_queue_persistence_fails() -> None:
    repository = InMemoryBillingRepository()
    service = BillingService(repository)
    service.grant_credits("account-1", 2, reference="purchase-1")

    with pytest.raises(RuntimeError, match="queue unavailable"):
        CreditAwareJobAdmission(service).admit(
            job_id="job-1",
            account_id="account-1",
            normalized_speech_characters=1001,
            admit=lambda: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
        )

    assert repository.account("account-1").available_credits == 2


def test_terminal_finalizer_settles_success_and_releases_other_terminal_outcomes() -> None:
    repository = InMemoryBillingRepository()
    service = BillingService(repository)
    service.grant_credits("account-1", 2, reference="purchase-1")
    authorization = service.reserve("job-1", "account-1", credits=1)
    service.reserve("job-2", "account-1", credits=1)
    finalizer = JobBillingFinalizer(service)

    finalizer.on_terminal("job-1", JobStatus.SUCCEEDED)
    finalizer.on_terminal("job-2", JobStatus.CANCELLED)

    assert repository.get_authorization(authorization.id).status is AuthorizationStatus.SETTLED
    assert repository.account("account-1").available_credits == 1
