"""Protected one-use mail claims and bounded daily cleanup."""

import hashlib
import hmac
from typing import Any

from kenkui_server.storage.postgres_database import PostgresDatabase


def claim_address(
    database: PostgresDatabase, environment: str, challenge: str, code: str
) -> str | None:
    digest = hashlib.sha256(f"kenkui-migration-v1\0{challenge}\0{code}".encode("ascii")).digest()
    with database.transaction():
        database.execute("SELECT pg_advisory_xact_lock_shared(915001,hashtext(%s))", (environment,))
        row = database.execute(
            """SELECT c.digest,r.address FROM migration_challenges c
               JOIN migration_directory_active a ON a.run_id=c.run_id AND a.environment=%s
               JOIN migration_directory_runs d ON d.id=a.run_id
               JOIN migration_directory_rows r ON r.run_id=c.run_id AND r.subject=c.old_subject
               JOIN provider_identities p ON p.issuer=d.issuer AND p.subject=r.subject
                    AND p.identity_id=r.owner_id
               WHERE c.id=%s AND r.owner_id=c.owner_id AND r.verified AND NOT r.banned
                 AND NOT EXISTS (SELECT 1 FROM migration_directory_rows other
                     WHERE other.run_id=r.run_id
                     AND other.subject<>r.subject AND other.address_key=r.address_key)
                 AND NOT EXISTS (SELECT 1 FROM migration_directory_aliases other
                     WHERE other.run_id=r.run_id
                     AND other.owner_id<>r.owner_id AND other.address_key=r.address_key)
                 AND c.consumed_at IS NULL AND c.superseded_at IS NULL AND c.claimed_at IS NULL
               FOR UPDATE OF c""",
            (environment, challenge),
        ).fetchone()
        if not row or not hmac.compare_digest(bytes(row["digest"]), digest):
            return None
        claimed = database.execute(
            """UPDATE migration_challenges c SET claimed_at=clock_timestamp()
               FROM migration_directory_runs d WHERE c.id=%s AND c.run_id=d.id
                 AND c.attempts<5 AND c.expires_at>clock_timestamp() AND c.claimed_at IS NULL
                 AND (d.mode='final' AND d.frozen OR d.valid_until>clock_timestamp())
               RETURNING c.id""",
            (challenge,),
        ).fetchone()
        return str(row["address"]) if claimed else None


def prune_migration_recovery(connection: Any, *, batch_size: int = 100) -> int:
    if not 1 <= batch_size <= 1000:
        raise ValueError("invalid migration prune limit")
    with connection.transaction():
        challenges = connection.execute(
            """DELETE FROM migration_challenges WHERE id IN
               (SELECT id FROM migration_challenges WHERE expires_at<=clock_timestamp()
                ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id""",
            (batch_size,),
        ).fetchall()
        count = len(challenges)
        if count < batch_size:
            events = connection.execute(
                """DELETE FROM migration_rate_events WHERE id IN
                   (SELECT id FROM migration_rate_events
                    WHERE created_at<=clock_timestamp()-interval '1 day'
                    ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id""",
                (batch_size - count,),
            ).fetchall()
            count += len(events)
        return count
