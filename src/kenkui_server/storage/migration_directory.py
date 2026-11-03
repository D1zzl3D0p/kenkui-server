"""Offline, target-specific reconciliation of provider and restored account evidence."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from kenkui_server.storage.migration_fixture_quarantine import (
    prior_classifications,
    validate_quarantine,
)

ENVIRONMENT_ID = "environment_01M0Y347Z9N6FYNTGVY3725X1Y"
CLIENT_ID = "client_01M0Y37EQMGD270AW5W7NZC1E0"
LEGACY_ISSUER = f"urn:kenkui:workos:{ENVIRONMENT_ID}:{CLIENT_ID}"
AUTHORITIES = dict.fromkeys(("staging", "production"), (ENVIRONMENT_ID, CLIENT_ID))
LOCAL = re.compile(r"[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+)*")
LABEL = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?")


def checked(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid migration evidence")


def mailbox(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("invalid migration mailbox")
    address = value.strip(" ")
    parts = address.split("@")
    checked(len(address) <= 254 and len(parts) == 2)
    local, domain = parts
    checked(len(local) <= 64 and LOCAL.fullmatch(local) is not None)
    labels = domain.split(".")
    checked(len(labels) >= 2 and all(LABEL.fullmatch(label) for label in labels))
    return address, address.casefold()


@dataclass(frozen=True)
class ImportResult:
    restored_owners: int
    source_only: int
    digest: str
    matched_restored_owners: int
    quarantined_owners: int
    quarantine_digest: str | None


def import_directory(
    connection: Any,
    environment: str,
    source: bytes,
    evidence: dict[str, Any],
    *,
    activate: bool,
    quarantine: bytes | None = None,
) -> ImportResult:
    authority = AUTHORITIES.get(environment)
    if authority is None or evidence.get("environment") != environment:
        raise ValueError("invalid migration authority")
    checked((evidence.get("sourceEnvironmentId"), evidence.get("sourceClientId")) == authority)
    digest = hashlib.sha256(source).hexdigest()
    checked(evidence.get("complete") is True and evidence.get("sourceDigest") == digest)
    mode = evidence.get("mode")
    watermark = evidence.get("watermark")
    checked(
        isinstance(watermark, str) and bool(watermark.strip()) and mode in ("provisional", "final")
    )
    frozen = evidence.get("admissionsFrozen") is True and evidence.get("writesFrozen") is True
    if mode == "final":
        checked(
            frozen
            and evidence.get("finalDigest") == digest
            and evidence.get("finalWatermark") == watermark
        )
    captured = datetime.fromisoformat(evidence["capturedAt"])
    checked(captured.utcoffset() is not None)
    valid_until = captured + timedelta(hours=24) if mode == "provisional" else None
    invitations = evidence.get("invitedEmails")
    if not isinstance(invitations, list):
        raise ValueError("missing invitation policy")
    invited = sorted({mailbox(value)[1] for value in invitations})
    export = json.loads(source)
    checked(export.get("authority") == {"environmentId": authority[0], "clientId": authority[1]})
    checked(export.get("capturedAt") == evidence["capturedAt"])
    checked(isinstance(export.get("source"), str) and bool(export["source"].strip()))
    listing = export["result"]["userlandUsers"]
    checked(listing.get("listMetadata") == {"before": None, "after": None})
    rows = listing["data"]
    checked(isinstance(rows, list) and bool(rows))
    count = evidence.get("expectedSourceCount")
    checked(type(count) is int and count == len(rows))
    subjects: dict[str, tuple[str, str, bool, bool]] = {}
    for row in rows:
        subject = row["id"]
        checked(
            isinstance(subject, str) and re.fullmatch(r"user_[A-Za-z0-9]+", subject) is not None
        )
        checked(subject not in subjects)
        address, key = mailbox(row["email"])
        verification = row["emailVerifiedAt"]
        if verification is not None:
            checked(
                isinstance(verification, str)
                and datetime.fromisoformat(verification).utcoffset() is not None
            )
        checked(type(row["banned"]) is bool)
        subjects[subject] = address, key, verification is not None, row["banned"]
    with connection.transaction():
        owner = connection.execute(
            "SELECT pg_get_userbyid(datdba) = current_user "
            "AND current_setting('transaction_isolation')='read committed' AS authorized "
            "FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        checked(bool(owner and owner["authorized"]))
        connection.execute("SELECT pg_advisory_xact_lock(915001,hashtext(%s))", (environment,))
        connection.execute("LOCK TABLE identities,provider_identities IN SHARE MODE")
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        checked(captured <= now and (valid_until is None or now < valid_until))
        restored = connection.execute(
            """SELECT i.id,i.email,i.workos_subject,p.identity_id AS bound FROM identities i
               LEFT JOIN provider_identities p ON p.issuer=%s AND p.subject=i.workos_subject
               WHERE i.workos_subject IS NOT NULL""",
            (LEGACY_ISSUER,),
        ).fetchall()
        owners = {r["workos_subject"]: r["id"] for r in restored}
        checked(len(owners) == len(restored))
        checked(all(r["bound"] == r["id"] for r in restored))
        aliases = [(r["id"], mailbox(r["email"])[1]) for r in restored if r["email"] is not None]
        conflicts = connection.execute(
            """SELECT 1 FROM provider_identities p JOIN identities i ON i.id=p.identity_id
               WHERE p.issuer=%s AND i.workos_subject IS DISTINCT FROM p.subject LIMIT 1""",
            (LEGACY_ISSUER,),
        ).fetchone()
        checked(conflicts is None)
        missing = {sub: owner for sub, owner in owners.items() if sub not in subjects}
        history = prior_classifications(connection, environment)
        if history:
            checked(quarantine is not None)
            checked(set(missing.items()) == {(r["subject"], r["owner_id"]) for r in history})
        manifest: dict[str, Any] = {}
        quarantine_digest = None
        if quarantine is None:
            checked(not missing)
        else:
            manifest, quarantine_digest = validate_quarantine(
                connection, environment, quarantine, evidence, missing, now, history
            )
        matched = len(restored) - len(missing)
        result = ImportResult(
            len(restored), len(subjects) - matched, digest, matched, len(missing), quarantine_digest
        )
        if activate:
            run = uuid4()
            connection.execute(
                """INSERT INTO migration_directory_runs
                   (id,environment,issuer,captured_at,watermark,digest,mode,valid_until,frozen,
                    invited_emails,restored_owners,source_only,matched_restored_owners,quarantined_owners,
                    quarantine_version,quarantine_digest,approval_artifact_digest,approval_ref,
                    attested_by,attested_at,restored_snapshot_digest)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    run,
                    environment,
                    LEGACY_ISSUER,
                    captured,
                    watermark,
                    digest,
                    mode,
                    valid_until,
                    frozen,
                    invited,
                    result.restored_owners,
                    result.source_only,
                    matched,
                    len(missing),
                    manifest.get("version"),
                    quarantine_digest,
                    manifest.get("approvalArtifactDigest"),
                    manifest.get("approvalRef"),
                    manifest.get("attestedBy"),
                    manifest.get("attestedAt"),
                    manifest.get("restoredSnapshotDigest"),
                ),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO migration_directory_rows
                       (run_id,subject,owner_id,address,address_key,verified,banned)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    [(run, sub, owners.get(sub), *values) for sub, values in subjects.items()],
                )
                cursor.executemany(
                    "INSERT INTO migration_directory_aliases(run_id,owner_id,address_key) "
                    "VALUES (%s,%s,%s)",
                    [(run, owner, key) for owner, key in aliases],
                )
                cursor.executemany(
                    """INSERT INTO migration_directory_quarantines
                       (run_id,owner_id,subject,fixture_fingerprint,finance_policy,legacy_finance_evidence)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
                    [
                        (
                            run,
                            r["ownerId"],
                            r["legacySubject"],
                            r["fixtureFingerprint"],
                            r.get("financePolicy", "strict-v1"),
                            json.dumps(r["legacyOpeningBalances"])
                            if r.get("legacyOpeningBalances")
                            else None,
                        )
                        for r in manifest.get("records", [])
                    ],
                )
            published = connection.execute(
                """INSERT INTO migration_directory_active(environment,run_id)
                   SELECT environment,id FROM migration_directory_runs
                   WHERE id=%s AND quarantined_owners=(SELECT count(*)
                       FROM migration_directory_quarantines WHERE run_id=%s)
                     AND (mode='final' OR valid_until>clock_timestamp())
                   ON CONFLICT (environment) DO UPDATE SET run_id=excluded.run_id
                   RETURNING (%s::timestamptz IS NULL OR clock_timestamp()<%s) AS fresh""",
                (run, run, valid_until, valid_until),
            ).fetchone()
            checked(bool(published and published["fresh"]))
        if valid_until is not None:
            checked(
                connection.execute(
                    "SELECT clock_timestamp()<%s AS fresh", (valid_until,)
                ).fetchone()["fresh"]
            )
        return result
