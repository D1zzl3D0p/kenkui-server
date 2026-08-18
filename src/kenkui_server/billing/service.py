"""Credit reservation policy independent of job runners and payment providers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from kenkui_server.billing.models import (
    AuthorizationStatus,
    BillingRepository,
    CreditAccount,
    CreditAuthorization,
)
from kenkui_server.billing.pricing import credits_for_characters, credits_for_text
from kenkui_server.jobs.models import JobStatus

T = TypeVar("T")


class BillingService:
    """Reserve before admission and finalize exactly once from terminal job state."""

    def __init__(self, repository: BillingRepository) -> None:
        self._repository = repository

    @staticmethod
    def credits_for_text(text: str) -> int:
        return credits_for_text(text)

    def grant_credits(self, account_id: str, credits: int, *, reference: str) -> CreditAccount:
        return self._repository.grant(account_id, credits, reference=reference)

    def reserve(self, job_id: str, account_id: str, *, credits: int) -> CreditAuthorization:
        return self._repository.reserve(job_id, account_id, credits)

    def finalize_success(self, job_id: str) -> CreditAuthorization:
        return self._repository.finalize(job_id, AuthorizationStatus.SETTLED)

    def release(self, job_id: str) -> CreditAuthorization:
        return self._repository.finalize(job_id, AuthorizationStatus.RELEASED)


class CreditAwareJobAdmission:
    """Reserve a job's credits before the supplied queue admission transaction."""

    def __init__(self, billing: BillingService) -> None:
        self._billing = billing

    def admit(
        self,
        *,
        job_id: str,
        account_id: str,
        normalized_speech_characters: int,
        admit: Callable[[], T],
    ) -> T:
        credits = credits_for_characters(normalized_speech_characters)
        if credits == 0:
            raise ValueError("empty_speech")
        self._billing.reserve(job_id, account_id, credits=credits)
        try:
            return admit()
        except Exception:
            self._billing.release(job_id)
            raise


class JobBillingFinalizer:
    """Maps terminal provider-neutral job state to one billing transition."""

    def __init__(self, billing: BillingService) -> None:
        self._billing = billing

    def on_terminal(self, job_id: str, status: JobStatus) -> CreditAuthorization:
        if status is JobStatus.SUCCEEDED:
            return self._billing.finalize_success(job_id)
        if status in {JobStatus.FAILED, JobStatus.CANCELLED}:
            return self._billing.release(job_id)
        raise ValueError("job_not_terminal")
