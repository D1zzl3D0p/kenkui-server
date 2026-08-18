"""Provider-neutral prepaid-credit records and an in-memory test repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import uuid4


class AuthorizationStatus(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class CreditAccount:
    id: str
    available_credits: int


@dataclass(frozen=True, slots=True)
class CreditAuthorization:
    id: str
    job_id: str
    account_id: str
    credits: int
    status: AuthorizationStatus


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: str
    account_id: str
    authorization_id: str | None
    kind: str
    credits: int
    reference: str
    created_at: datetime


class BillingRepository(Protocol):
    """Atomic storage operations required by the credit policy."""

    def account(self, account_id: str) -> CreditAccount: ...

    def grant(self, account_id: str, credits: int, *, reference: str) -> CreditAccount: ...

    def reserve(self, job_id: str, account_id: str, credits: int) -> CreditAuthorization: ...

    def authorization_for_job(self, job_id: str) -> CreditAuthorization: ...

    def finalize(self, job_id: str, status: AuthorizationStatus) -> CreditAuthorization: ...

    def ledger_for_authorization(self, authorization_id: str) -> tuple[LedgerEntry, ...]: ...


class InMemoryBillingRepository:
    """Deterministic fake with the same idempotency invariants as hosted storage."""

    def __init__(self) -> None:
        self._accounts: dict[str, CreditAccount] = {}
        self._authorizations: dict[str, CreditAuthorization] = {}
        self._authorization_by_job: dict[str, str] = {}
        self._entries: list[LedgerEntry] = []
        self._references: set[str] = set()

    def account(self, account_id: str) -> CreditAccount:
        return self._accounts.get(account_id, CreditAccount(account_id, 0))

    def grant(self, account_id: str, credits: int, *, reference: str) -> CreditAccount:
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        if reference in self._references:
            return self.account(account_id)
        account = self.account(account_id)
        updated = CreditAccount(account_id, account.available_credits + credits)
        self._accounts[account_id] = updated
        self._references.add(reference)
        self._entries.append(
            LedgerEntry(str(uuid4()), account_id, None, "purchase", credits, reference, datetime.now(UTC))
        )
        return updated

    def reserve(self, job_id: str, account_id: str, credits: int) -> CreditAuthorization:
        existing_id = self._authorization_by_job.get(job_id)
        if existing_id is not None:
            return self._authorizations[existing_id]
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        account = self.account(account_id)
        if account.available_credits < credits:
            raise ValueError("insufficient_credits")
        authorization = CreditAuthorization(
            str(uuid4()), job_id, account_id, credits, AuthorizationStatus.RESERVED
        )
        self._accounts[account_id] = CreditAccount(account_id, account.available_credits - credits)
        self._authorizations[authorization.id] = authorization
        self._authorization_by_job[job_id] = authorization.id
        self._entries.append(
            LedgerEntry(
                str(uuid4()), account_id, authorization.id, "reservation", -credits, job_id, datetime.now(UTC)
            )
        )
        return authorization

    def authorization_for_job(self, job_id: str) -> CreditAuthorization:
        return self._authorizations[self._authorization_by_job[job_id]]

    def get_authorization(self, authorization_id: str) -> CreditAuthorization:
        return self._authorizations[authorization_id]

    def finalize(self, job_id: str, status: AuthorizationStatus) -> CreditAuthorization:
        authorization = self.authorization_for_job(job_id)
        if authorization.status is not AuthorizationStatus.RESERVED:
            return authorization
        finalized = CreditAuthorization(
            authorization.id, authorization.job_id, authorization.account_id, authorization.credits, status
        )
        self._authorizations[authorization.id] = finalized
        if status is AuthorizationStatus.SETTLED:
            kind, credits = "settlement", 0
        elif status is AuthorizationStatus.RELEASED:
            kind, credits = "release", authorization.credits
            account = self.account(authorization.account_id)
            self._accounts[account.id] = CreditAccount(account.id, account.available_credits + credits)
        else:
            raise ValueError("invalid_final_authorization_status")
        self._entries.append(
            LedgerEntry(
                str(uuid4()), authorization.account_id, authorization.id, kind, credits, job_id, datetime.now(UTC)
            )
        )
        return finalized

    def ledger_for_authorization(self, authorization_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.authorization_id == authorization_id)
