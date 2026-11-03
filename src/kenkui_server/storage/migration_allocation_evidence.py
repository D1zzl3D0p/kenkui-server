"""Read-only reconciliation of authorization allocations and original opening evidence."""

import hashlib
from typing import Any
from uuid import UUID


def allocation_exceptions(conn: Any, owner: UUID | None = None) -> tuple[int, list[dict[str, Any]]]:
    conn.execute("SET LOCAL TIME ZONE 'UTC'")
    missing = conn.execute(
        """
        SELECT z.*,to_jsonb(z)::text AS row_bytes
        FROM credit_authorizations z JOIN credit_accounts ca ON ca.id=z.account_id
        LEFT JOIN LATERAL (
            SELECT count(*) AS n, sum(a.credits) AS credits,
                   bool_and(a.account_id=z.account_id AND
                            l.account_id IS NOT DISTINCT FROM z.account_id) AS same_account
            FROM credit_allocations a LEFT JOIN credit_lots l ON l.id=a.lot_id
            WHERE a.authorization_id=z.id
        ) allocation ON true
        WHERE (%s::uuid IS NULL OR ca.identity_id=%s)
          AND (allocation.n=0 OR allocation.credits<>z.credits
               OR allocation.same_account IS NOT TRUE)
        ORDER BY z.id""",
        (owner, owner),
    ).fetchall()
    historical = []
    for row in missing:
        allocations = conn.execute(
            "SELECT count(*) AS n FROM credit_allocations WHERE authorization_id=%s", (row["id"],)
        ).fetchone()["n"]
        if row["status"] not in ("settled", "released") or allocations != 0:
            continue
        lot = conn.execute(
            (
                "SELECT id,reference,created_at FROM credit_lots WHERE account_id=%s AND "
                "kind='legacy' ORDER BY id"
            ),
            (row["account_id"],),
        ).fetchall()
        chronology = (
            len(lot) == 1
            and row["finalized_at"] is not None
            and row["created_at"] <= row["finalized_at"] < lot[0]["created_at"]
            and lot[0]["id"] == lot[0]["reference"] == "legacy:" + str(row["account_id"])
        )
        historical.append(
            {
                "authorizationId": str(row["id"]),
                "accountId": str(row["account_id"]),
                "jobId": row["job_id"],
                "credits": str(row["credits"]),
                "status": row["status"],
                "createdAt": row["created_at"].isoformat(),
                "finalizedAt": row["finalized_at"].isoformat() if row["finalized_at"] else None,
                "legacyLots": [
                    {
                        "id": x["id"],
                        "reference": x["reference"],
                        "createdAt": x["created_at"].isoformat(),
                    }
                    for x in lot
                ],
                "sourceRowDigest": hashlib.sha256(
                    row["row_bytes"].encode("utf8") + b"\n"
                ).hexdigest(),
                "chronologyValid": chronology,
            }
        )
    return len(missing), historical
