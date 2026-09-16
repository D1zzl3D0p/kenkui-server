"""Credit-pack allocation inside the caller's account-locked transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from kenkui_server.billing.models import AuthorizationStatus, CreditLot

if TYPE_CHECKING:
    from kenkui_server.storage.postgres import PostgresConnection


class PostgresCreditLots:
    def __init__(self, connection: PostgresConnection) -> None:
        self.connection = connection

    def list(self, account_id: str) -> tuple[CreditLot, ...]:
        rows = self.connection.execute(
            "SELECT id, account_id, kind, reference, credited, reserved, consumed, created_at "
            "FROM credit_lots WHERE account_id=%s ORDER BY sequence",
            (account_id,),
        ).fetchall()
        return tuple(
            CreditLot(
                str(row["id"]),
                str(row["account_id"]),
                str(row["kind"]),
                str(row["reference"]),
                int(row["credited"]),
                int(row["reserved"]),
                int(row["consumed"]),
                row["created_at"],
            )
            for row in rows
        )

    def grant(self, account_id: str, credits: int, reference: str, kind: str) -> None:
        self.connection.execute(
            "INSERT INTO credit_lots (id, account_id, kind, reference, credited) "
            "VALUES (%s,%s,%s,%s,%s)",
            (str(uuid4()), account_id, kind, reference, credits),
        )

    def reserve(self, authorization_id: str, account_id: str, credits: int) -> None:
        rows = self.connection.execute(
            "SELECT id, credited-reserved-consumed AS available FROM credit_lots "
            "WHERE account_id=%s AND credited > reserved+consumed "
            "ORDER BY (kind='purchase'), sequence FOR UPDATE",
            (account_id,),
        ).fetchall()
        remaining = credits
        for row in rows:
            amount = min(remaining, int(row["available"]))
            self.connection.execute(
                "UPDATE credit_lots SET reserved=reserved+%s WHERE id=%s",
                (amount, row["id"]),
            )
            self.connection.execute(
                "INSERT INTO credit_allocations (authorization_id, lot_id, account_id, credits) "
                "VALUES (%s,%s,%s,%s)",
                (authorization_id, row["id"], account_id, amount),
            )
            remaining -= amount
            if not remaining:
                return
        # Roll back admission, including the aggregate balance debit. Never let
        # an untracked balance silently make a newly purchased pack look unused.
        raise ValueError("credit_lot_balance_mismatch")

    def finalize(
        self, authorization_id: str, account_id: str, credits: int, status: AuthorizationStatus
    ) -> None:
        rows = self.connection.execute(
            "SELECT lot_id, credits FROM credit_allocations "
            "WHERE authorization_id=%s AND account_id=%s",
            (authorization_id, account_id),
        ).fetchall()
        if sum(int(row["credits"]) for row in rows) != credits:
            raise ValueError("credit_lot_balance_mismatch")
        for row in rows:
            amount = int(row["credits"])
            result = self.connection.execute(
                "UPDATE credit_lots SET reserved=reserved-%s, consumed=consumed+%s "
                "WHERE id=%s AND account_id=%s AND reserved >= %s",
                (
                    amount,
                    amount if status is AuthorizationStatus.SETTLED else 0,
                    row["lot_id"],
                    account_id,
                    amount,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("credit_lot_balance_mismatch")
