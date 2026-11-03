"""Release-only v1 target capture and exact reviewed fixture reconciliation."""

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import sql

ASSERTION = "owner-approved-synthetic-fixtures-preserve-all-exclude-recovery"
APPROVAL_KEYS = ("approvalArtifactDigest", "approvalRef", "attestedBy", "attestedAt", "records")
MANIFEST_KEYS = {
    *APPROVAL_KEYS,
    "version",
    "environment",
    "sourceEnvironmentId",
    "sourceClientId",
    "sourceDigest",
    "restoredSnapshotDigest",
    "assertion",
}
# Explicit restored application relations, independent of migration/recovery workflow churn.
INVENTORY = (
    "artifacts",
    "assets",
    "credit_accounts",
    "credit_allocations",
    "credit_authorizations",
    "credit_ledger_entries",
    "credit_lots",
    "dispatches",
    "execution_leases",
    "identities",
    "inspections",
    "job_checkpoints",
    "job_events",
    "job_failures",
    "job_idempotency",
    "job_notifications",
    "jobs",
    "payment_events",
    "provider_identities",
    "source_upload_intents",
    "stored_objects",
)


def require(value: bool) -> None:
    if not value:
        raise ValueError("invalid fixture evidence")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = dict(pairs)
    require(len(obj) == len(pairs))
    return obj


def load_json(raw: bytes) -> Any:
    return json.loads(raw, object_pairs_hook=_unique_object)


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _lock_capture(conn: Any) -> None:
    authorized = conn.execute("""SELECT datdba=current_user::regrole AS owner,
        current_setting('transaction_isolation')='read committed' AS fresh
        FROM pg_database WHERE datname=current_database()""").fetchone()
    require(authorized["owner"] and authorized["fresh"])
    conn.execute(
        sql.SQL("LOCK TABLE {} IN SHARE MODE").format(
            sql.SQL(",").join(map(sql.Identifier, INVENTORY))
        )
    )


def restored_snapshot_digest(conn: Any) -> str:
    """Post-backfill target capture v1; nested transaction retains the outer release locks."""
    with conn.transaction():
        _lock_capture(conn)
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        conn.execute("SET LOCAL extra_float_digits=3")
        digest = hashlib.sha256(b"kenkui-restored-target-v1\0")
        for table in INVENTORY:
            digest.update(table.encode("ascii") + b"\0")
            # JSONB escapes embedded newlines. C ordering is independent of database locale.
            with conn.cursor(name="quarantine_snapshot") as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT to_jsonb(t)::text AS line FROM {} t "
                        'ORDER BY to_jsonb(t)::text COLLATE "C"'
                    ).format(sql.Identifier(table))
                )
                for row in cur:
                    digest.update(row["line"].encode("utf8") + b"\n")
        return digest.hexdigest()


# Every query has a single owner parameter and deterministic primary-key order. Text PKs
# explicitly use C collation. Selected numerics and UUIDs below become canonical strings.
SELECTORS = {
    "identity": "SELECT id,workos_subject,email FROM identities WHERE id=%s",
    "bindings": """SELECT issuer,subject,identity_id FROM provider_identities WHERE identity_id=%s
                   ORDER BY issuer COLLATE "C",subject COLLATE "C" """,
    "jobs": """SELECT id,owner_id,spec_json,status FROM jobs WHERE owner_id=%s
               ORDER BY id COLLATE "C" """,
    "assets": """SELECT id,owner_id,path,sha256,format,deleted_at IS NOT NULL AS deleted,
                    cover_operation_id,cover_generation FROM assets WHERE owner_id=%s
                    ORDER BY id COLLATE "C" """,
    "inspections": """SELECT i.* FROM inspections i JOIN assets a ON a.id=i.source_id
                      WHERE a.owner_id=%s ORDER BY i.source_id COLLATE "C" """,
    "artifacts": """SELECT a.* FROM artifacts a JOIN jobs j ON j.id=a.job_id WHERE j.owner_id=%s
                    ORDER BY a.id COLLATE "C" """,
    "objects": """WITH owner_key AS (SELECT %s::uuid AS id)
                  SELECT o.id,o.object_kind,o.opaque_object_key,o.resource_id,
                         o.deleted_at IS NOT NULL AS deleted
                  FROM stored_objects o WHERE (o.object_kind,o.resource_id) IN (
                      SELECT 'source',a.id FROM assets a,owner_key WHERE a.owner_id=owner_key.id
                      UNION SELECT 'artifact',a.path FROM artifacts a
                      JOIN jobs j ON j.id=a.job_id,owner_key
                        WHERE j.owner_id=owner_key.id)
                  ORDER BY o.id""",
    "intents": """SELECT source_id,owner_id,sha256,cover_operation_id,cover_generation
                  FROM source_upload_intents WHERE owner_id=%s ORDER BY source_id COLLATE "C" """,
    "accounts": """SELECT id,identity_id,available_credits FROM credit_accounts
                   WHERE identity_id=%s ORDER BY id""",
    "authorizations": """SELECT z.id,z.job_id,z.account_id,z.credits,z.status,
                                j.owner_id AS job_owner,a.identity_id AS account_owner
                         FROM credit_authorizations z JOIN credit_accounts a ON a.id=z.account_id
                         JOIN jobs j ON j.id=z.job_id WHERE %s IN (a.identity_id,j.owner_id)
                         ORDER BY z.id""",
    "ledger": """SELECT e.id,e.account_id,e.authorization_id,e.kind,e.credits,e.reference
                 FROM credit_ledger_entries e JOIN credit_accounts a ON a.id=e.account_id
                 LEFT JOIN credit_authorizations z ON z.id=e.authorization_id
                 LEFT JOIN credit_accounts za ON za.id=z.account_id
                 WHERE %s IN (a.identity_id,za.identity_id) ORDER BY e.id""",
    "lots": """SELECT l.id,l.sequence,l.account_id,l.kind,l.reference,l.credited,
                      l.reserved,l.consumed
               FROM credit_lots l JOIN credit_accounts a ON a.id=l.account_id
               WHERE a.identity_id=%s ORDER BY l.id COLLATE "C" """,
    "allocations": """SELECT l.authorization_id,l.lot_id,l.account_id,l.credits
                      FROM credit_allocations l
                      JOIN credit_accounts a ON a.id=l.account_id WHERE a.identity_id=%s
                      ORDER BY l.authorization_id,l.lot_id COLLATE "C" """,
    "payments": """SELECT provider,provider_event_id FROM payment_events
                   WHERE provider||':'||provider_event_id IN
                    (SELECT e.reference FROM credit_ledger_entries e
                     JOIN credit_accounts a ON a.id=e.account_id WHERE a.identity_id=%(owner)s
                     UNION SELECT l.reference FROM credit_lots l
                     JOIN credit_accounts a ON a.id=l.account_id WHERE a.identity_id=%(owner)s)
                    ORDER BY provider COLLATE "C",provider_event_id COLLATE "C" """,
}


def _projection(conn: Any, environment: str, owner: UUID) -> dict[str, Any]:
    data: dict[str, Any] = {"environment": environment}
    for name, query in SELECTORS.items():
        params = {"owner": owner} if name == "payments" else (owner,)
        data[name] = [
            {k: str(v) if isinstance(v, UUID) or type(v) is int else v for k, v in row.items()}
            for row in conn.execute(query, params)
        ]
    return data


def _fingerprint(projection: dict[str, Any]) -> str:
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def fixture_fingerprint(conn: Any, environment: str, owner: UUID) -> str:
    """Private capture of classification inputs. A digest by itself confers no approval."""
    with conn.transaction():
        _lock_capture(conn)
        return _fingerprint(_projection(conn, environment, owner))


def _validate_owner(conn: Any, environment: str, owner: UUID, fingerprint: str) -> None:
    data = _projection(conn, environment, owner)
    require(_fingerprint(data) == fingerprint)
    require(len(data["identity"]) == 1 and data["identity"][0]["email"] is None)
    require(len(data["bindings"]) == 1 and not data["payments"])
    require(all(j["status"] in ("succeeded", "failed", "cancelled") for j in data["jobs"]))
    balances = {a["id"]: int(a["available_credits"]) for a in data["accounts"]}
    available_from_lots = dict.fromkeys(balances, 0)
    ledger_balances = dict.fromkeys(balances, 0)
    grants = set()
    for lot in data["lots"]:
        require(lot["kind"] != "purchase" and not lot["reference"].startswith("stripe:"))
        credited = int(lot["credited"])
        # The 0004 carryover aggregates pre-lot balances with unknown origin.
        require(lot["kind"] != "legacy" or credited == 0)
        available_from_lots[lot["account_id"]] += (
            credited - int(lot["reserved"]) - int(lot["consumed"])
        )
        if lot["kind"] == "grant":
            grants.add((lot["account_id"], lot["reference"], lot["credited"]))
    authorizations = {}
    for auth in data["authorizations"]:
        require(auth["account_owner"] == auth["job_owner"] == str(owner))
        authorizations[auth["id"]] = auth["account_id"]
    for entry in data["ledger"]:
        account = entry["account_id"]
        require(account in balances and not entry["reference"].startswith("stripe:"))
        ledger_balances[account] += int(entry["credits"])
        if entry["authorization_id"] is not None:
            require(authorizations.get(entry["authorization_id"]) == account)
        # Ordinary grants also produce purchase-kind ledger entries.
        if entry["kind"] == "purchase":
            origin = (account, entry["reference"], entry["credits"])
            require(entry["authorization_id"] is None and origin in grants)
            grants.remove(origin)
    require(not grants and balances == available_from_lots == ledger_balances)
    blocked = conn.execute(
        """
        SELECT 1 FROM identity_link_proofs WHERE identity_id=%s AND consumed_at IS NULL
            AND expires_at>clock_timestamp()
        UNION ALL
        SELECT 1 FROM migration_challenges c JOIN migration_directory_active a ON a.run_id=c.run_id
            JOIN migration_directory_runs d ON d.id=a.run_id WHERE c.owner_id=%s
            AND c.consumed_at IS NULL AND c.superseded_at IS NULL AND c.attempts<5
            AND c.expires_at>clock_timestamp()
            AND (d.mode='final' OR d.valid_until>clock_timestamp())
        LIMIT 1""",
        (owner, owner),
    ).fetchone()
    require(blocked is None)


def prior_classifications(conn: Any, environment: str) -> list[dict[str, Any]]:
    # Inactive runs keep the classification even if a pointer is lost.
    rows: list[dict[str, Any]] = conn.execute(
        """
        SELECT DISTINCT q.owner_id,q.subject,q.fixture_fingerprint,r.approval_ref,
               a.run_id IS NOT NULL AS active
        FROM migration_directory_quarantines q JOIN migration_directory_runs r ON r.id=q.run_id
        LEFT JOIN migration_directory_active a ON a.run_id=q.run_id
        WHERE r.environment=%s""",
        (environment,),
    ).fetchall()
    return rows


def validate_quarantine(
    conn: Any,
    environment: str,
    raw: bytes,
    evidence: dict[str, Any],
    missing: dict[str, UUID],
    now: datetime,
    history: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Return verified manifest and digest after exhaustive pair and locked target checks."""
    require(isinstance(raw, bytes))
    digest = hashlib.sha256(raw).hexdigest()
    require(evidence.get("quarantineManifestDigest") == digest)
    data = load_json(raw)
    require(isinstance(data, dict) and data.keys() == MANIFEST_KEYS)
    require(
        type(data["version"]) is int and data["version"] == 1 and data["assertion"] == ASSERTION
    )
    require(
        all(
            data[k] == evidence.get(k)
            for k in (
                "environment",
                "sourceEnvironmentId",
                "sourceClientId",
                "sourceDigest",
                "restoredSnapshotDigest",
            )
        )
    )
    require(_digest(data["approvalArtifactDigest"]) and _digest(data["restoredSnapshotDigest"]))
    require(evidence.get("quarantineApproval") == {k: data[k] for k in APPROVAL_KEYS})
    require(
        all(
            isinstance(data[k], str) and data[k].strip() and data[k].isprintable()
            for k in ("approvalRef", "attestedBy")
        )
    )
    require(isinstance(data["attestedAt"], str))
    when = datetime.fromisoformat(data["attestedAt"])
    require(
        when.utcoffset() is not None
        and datetime.fromisoformat(evidence["capturedAt"]) <= when <= now
    )
    if evidence["mode"] == "final":
        require(evidence.get("finalRestoredSnapshotDigest") == data["restoredSnapshotDigest"])
    require(isinstance(data["records"], list) and len(data["records"]) == len(missing) > 0)
    require(restored_snapshot_digest(conn) == data["restoredSnapshotDigest"])
    conn.execute("LOCK TABLE identity_link_proofs,migration_challenges IN SHARE MODE")
    remaining = dict(missing)
    for entry in data["records"]:
        require(
            isinstance(entry, dict)
            and entry.keys() == {"ownerId", "legacySubject", "fixtureFingerprint"}
        )
        sub = entry["legacySubject"]
        require(
            isinstance(sub, str)
            and bool(sub.strip())
            and sub.isprintable()
            and not any(c in sub for c in "*?[]")
            and sub in remaining
            and str(remaining[sub]) == entry["ownerId"]
        )
        require(_digest(entry["fixtureFingerprint"]))
        _validate_owner(conn, environment, remaining.pop(sub), entry["fixtureFingerprint"])
    # An operator can rebind unchanged reviewed records to a new snapshot. A change
    # needs a fresh durable decision; replaying a formerly active approval is insufficient.
    fingerprints = {r["legacySubject"]: r["fixtureFingerprint"] for r in data["records"]}
    active = [r for r in history if r["active"]]
    if any(fingerprints[r["subject"]] != r["fixture_fingerprint"] for r in (active or history)):
        require(all(r["approval_ref"] != data["approvalRef"] for r in history))
    for r in history:
        if r["approval_ref"] == data["approvalRef"]:
            require(fingerprints[r["subject"]] == r["fixture_fingerprint"])
    return data, digest
