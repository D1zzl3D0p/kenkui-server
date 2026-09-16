"""Provider-neutral prepaid-credit records and an in-memory test repository."""

from __future__ import annotations

from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class CreditLot:
    """One grant or paid pack, with bonus credits included in its total."""

    id: str
    account_id: str
    kind: str
    reference: str
    credited: int
    reserved: int
    consumed: int
    created_at: datetime

    @property
    def available(self) -> int:
        return self.credited - self.reserved - self.consumed

    @property
    def usage_status(self) -> str:
        if self.kind == "legacy":
            return "manual_review"
        if self.kind != "purchase":
            return "not_purchased"
        if self.consumed:
            return "used"
        return "reserved" if self.reserved else "unused"


class BillingRepository(Protocol):
    """Atomic storage operations required by the credit policy."""

    def account(self, account_id: str) -> CreditAccount: ...

    def credit_lots(self, account_id: str) -> tuple[CreditLot, ...]: ...

    def grant(self, account_id: str, credits: int, *, reference: str) -> CreditAccount: ...

    def process_payment_event(
        self, provider: str, provider_event_id: str, account_id: str, credits: int
    ) -> CreditAccount: ...

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
        self._processed_payment_events: set[tuple[str, str]] = set()
        self._lots: dict[str, CreditLot] = {}
        self._allocations: dict[str, dict[str, int]] = {}

    def account(self, account_id: str) -> CreditAccount:
        return self._accounts.get(account_id, CreditAccount(account_id, 0))

    def grant(self, account_id: str, credits: int, *, reference: str) -> CreditAccount:
        return self._grant(account_id, credits, reference=reference, kind="grant")

    def credit_lots(self, account_id: str) -> tuple[CreditLot, ...]:
        return tuple(lot for lot in self._lots.values() if lot.account_id == account_id)

    def _grant(
        self, account_id: str, credits: int, *, reference: str, kind: str
    ) -> CreditAccount:
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        if reference in self._references:
            lot = next(lot for lot in self._lots.values() if lot.reference == reference)
            if (lot.account_id, lot.credited, lot.kind) != (account_id, credits, kind):
                raise ValueError("payment_reference_conflict")
            return self.account(account_id)
        account = self.account(account_id)
        updated = CreditAccount(account_id, account.available_credits + credits)
        self._accounts[account_id] = updated
        self._references.add(reference)
        lot = CreditLot(str(uuid4()), account_id, kind, reference, credits, 0, 0, datetime.now(UTC))
        self._lots[lot.id] = lot
        self._entries.append(
            LedgerEntry(
                str(uuid4()), account_id, None, "purchase", credits, reference, datetime.now(UTC)
            )
        )
        return updated

    def process_payment_event(
        self, provider: str, provider_event_id: str, account_id: str, credits: int
    ) -> CreditAccount:
        event = (provider, provider_event_id)
        account = self._grant(
            account_id, credits, reference=f"{provider}:{provider_event_id}", kind="purchase"
        )
        self._processed_payment_events.add(event)
        return account

    def reserve(self, job_id: str, account_id: str, credits: int) -> CreditAuthorization:
        existing_id = self._authorization_by_job.get(job_id)
        if existing_id is not None:
            existing = self._authorizations[existing_id]
            if (existing.account_id, existing.credits) != (account_id, credits):
                raise ValueError("authorization_conflict")
            return existing
        if credits < 1:
            raise ValueError("invalid_credit_amount")
        account = self.account(account_id)
        if account.available_credits < credits:
            raise ValueError("insufficient_credits")
        authorization = CreditAuthorization(
            str(uuid4()), job_id, account_id, credits, AuthorizationStatus.RESERVED
        )
        remaining = credits
        allocations: dict[str, int] = {}
        for lot in sorted(self.credit_lots(account_id), key=lambda lot: lot.kind == "purchase"):
            amount = min(remaining, lot.available)
            if amount:
                allocations[lot.id] = amount
                remaining -= amount
            if not remaining:
                break
        if remaining:
            raise ValueError("credit_lot_balance_mismatch")
        for lot_id, amount in allocations.items():
            lot = self._lots[lot_id]
            self._lots[lot_id] = replace(lot, reserved=lot.reserved + amount)
        self._allocations[authorization.id] = allocations
        self._accounts[account_id] = CreditAccount(account_id, account.available_credits - credits)
        self._authorizations[authorization.id] = authorization
        self._authorization_by_job[job_id] = authorization.id
        self._entries.append(
            LedgerEntry(
                str(uuid4()),
                account_id,
                authorization.id,
                "reservation",
                -credits,
                job_id,
                datetime.now(UTC),
            )
        )
        return authorization

    def authorization_for_job(self, job_id: str) -> CreditAuthorization:
        return self._authorizations[self._authorization_by_job[job_id]]

    def get_authorization(self, authorization_id: str) -> CreditAuthorization:
        return self._authorizations[authorization_id]

    def finalize(self, job_id: str, status: AuthorizationStatus) -> CreditAuthorization:
        if status not in {AuthorizationStatus.SETTLED, AuthorizationStatus.RELEASED}:
            raise ValueError("invalid_final_authorization_status")
        authorization = self.authorization_for_job(job_id)
        if authorization.status is not AuthorizationStatus.RESERVED:
            return authorization
        finalized = CreditAuthorization(
            authorization.id,
            authorization.job_id,
            authorization.account_id,
            authorization.credits,
            status,
        )
        self._authorizations[authorization.id] = finalized
        for lot_id, amount in self._allocations[authorization.id].items():
            lot = self._lots[lot_id]
            self._lots[lot_id] = replace(
                lot, reserved=lot.reserved - amount,
                consumed=lot.consumed + (amount if status is AuthorizationStatus.SETTLED else 0),
            )
        if status is AuthorizationStatus.SETTLED:
            kind, credits = "settlement", 0
        elif status is AuthorizationStatus.RELEASED:
            kind, credits = "release", authorization.credits
            account = self.account(authorization.account_id)
            self._accounts[account.id] = CreditAccount(
                account.id, account.available_credits + credits
            )
        else:
            raise ValueError("invalid_final_authorization_status")
        self._entries.append(
            LedgerEntry(
                str(uuid4()),
                authorization.account_id,
                authorization.id,
                kind,
                credits,
                job_id,
                datetime.now(UTC),
            )
        )
        return finalized

    def ledger_for_authorization(self, authorization_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.authorization_id == authorization_id)
