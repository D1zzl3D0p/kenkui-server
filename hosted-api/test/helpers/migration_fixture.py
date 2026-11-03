"""Disposable database only. Synthetic directory and mail bridge exercise."""

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime

from kenkui_server.storage.migration_directory import CLIENT_ID, ENVIRONMENT_ID, import_directory
from kenkui_server.storage.postgres_database import PostgresDatabase

url = "postgresql://postgres@127.0.0.1:55438/postgres"
assert os.environ["KENKUI_TEST_DATABASE_URL"] == url
name, action, *args = sys.argv[1:]
assert re.fullmatch("migration_test_[a-f0-9]{32}", name)
db = PostgresDatabase(url.removesuffix("postgres") + name)
try:
    if action in ("activate", "quarantine"):
        captured = datetime.now(UTC).isoformat()
        rows = [
            {"id": sub, "email": addr, "emailVerifiedAt": captured, "banned": False}
            for sub, addr in [("user_old", "Legacy@Old.test"), ("user_new", "source@new.test")]
        ]
        raw = json.dumps(
            {
                "authority": {"environmentId": ENVIRONMENT_ID, "clientId": CLIENT_ID},
                "capturedAt": captured,
                "source": "fixture",
                "result": {
                    "userlandUsers": {"data": rows, "listMetadata": {"before": None, "after": None}}
                },
            }
        ).encode()
        with db.connection() as conn:
            evidence = {
                "environment": "staging",
                "sourceEnvironmentId": ENVIRONMENT_ID,
                "sourceClientId": CLIENT_ID,
                "sourceDigest": hashlib.sha256(raw).hexdigest(),
                "complete": True,
                "mode": "provisional",
                "watermark": "fixture",
                "capturedAt": captured,
                "expectedSourceCount": 2,
                "invitedEmails": [],
            }
            q = None
            if action == "quarantine":
                from uuid import UUID

                from kenkui_server.storage.migration_directory import LEGACY_ISSUER
                from kenkui_server.storage.migration_fixture_quarantine import (
                    fixture_fingerprint,
                    restored_snapshot_digest,
                )

                owner = UUID(int=7000)
                sub = "user_fixture"
                conn.execute(
                    "INSERT INTO identities(id,workos_subject) VALUES (%s,%s)", (owner, sub)
                )
                conn.execute(
                    "INSERT INTO provider_identities(issuer,subject,identity_id) VALUES (%s,%s,%s)",
                    (LEGACY_ISSUER, sub, owner),
                )
                account = UUID(int=7001)
                lot_id = f"legacy:{account}"
                conn.execute(
                    "INSERT INTO credit_accounts(id,identity_id,available_credits) "
                    "VALUES (%s,%s,4)",
                    (account, owner),
                )
                conn.execute(
                    """INSERT INTO credit_lots(id,account_id,kind,reference,credited)
                                VALUES (%s,%s,'legacy',%s,4)""",
                    (lot_id, account, lot_id),
                )
                conn.execute(
                    """INSERT INTO credit_ledger_entries(id,account_id,kind,credits,reference)
                        VALUES (gen_random_uuid(),%s,'reservation',-3,'fictional-reservation')""",
                    (account,),
                )
                snapshot = restored_snapshot_digest(conn)
                approval = {
                    "approvalArtifactDigest": "c" * 64,
                    "approvalRef": "synthetic/review",
                    "attestedBy": "synthetic-release",
                    "attestedAt": datetime.now(UTC).isoformat(),
                    "records": [
                        {
                            "ownerId": str(owner),
                            "legacySubject": sub,
                            "fixtureFingerprint": fixture_fingerprint(conn, "staging", owner),
                            "financePolicy": "preserve-approved-unknown-legacy",
                            "legacyOpeningBalances": [
                                {
                                    "accountId": str(account),
                                    "availableCredits": "4",
                                    "netLedgerCredits": "-3",
                                    "openingResidualCredits": "7",
                                    "legacyLots": [
                                        {
                                            "lotId": lot_id,
                                            "reference": lot_id,
                                            "credited": "4",
                                            "reserved": "0",
                                            "consumed": "0",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
                q = json.dumps(
                    {
                        **approval,
                        "version": 2,
                        "environment": "staging",
                        "sourceEnvironmentId": ENVIRONMENT_ID,
                        "sourceClientId": CLIENT_ID,
                        "sourceDigest": evidence["sourceDigest"],
                        "restoredSnapshotDigest": snapshot,
                        "assertion": (
                            "owner-approved-synthetic-fixtures-preserve-all-exclude-recovery"
                        ),
                    }
                ).encode()
                evidence.update(
                    restoredSnapshotDigest=snapshot,
                    quarantineApproval=approval,
                    quarantineManifestDigest=hashlib.sha256(q).hexdigest(),
                )
            import_directory(conn, "staging", raw, evidence, activate=True, quarantine=q)
    elif action in ("deliver", "fail_send", "slow_send"):
        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from kenkui_server.compute.api_bridge import create_bridge_app
        from kenkui_server.notifications.mailer import Message, RecordingMailer

        class CommittedMailer(RecordingMailer):
            def send(self, message: Message) -> None:
                assert getattr(db._state, "connection", None) is None
                row = db.execute(
                    "SELECT claimed_at FROM migration_challenges WHERE id=%s", (args[0],)
                ).fetchone()
                assert row["claimed_at"] is not None
                assert message.to == "Legacy@Old.test" and args[1] in message.text
                if action == "fail_send":
                    raise RuntimeError("synthetic private delivery failure")
                if action == "slow_send":
                    import time

                    time.sleep(0.5)
                super().send(message)

        mailer = CommittedMailer()
        app = create_bridge_app(
            environment="staging",
            secret="fixture",
            migration_mailer=lambda: mailer,
            request_timeout_seconds=0.25 if action == "slow_send" else 120,
            services_factory=lambda: SimpleNamespace(database=db),
        )
        with TestClient(app) as client:

            def send_once(_):
                response = client.post(
                    "/migration-email",
                    headers={"X-Kenkui-Bridge-Secret": "fixture"},
                    json={"environment": "staging", "challengeId": args[0], "code": args[1]},
                )
                assert args[1] not in response.text and args[0] not in response.text
                assert "private" not in response.text and "@Old.test" not in response.text
                return response.status_code

            with ThreadPoolExecutor(max_workers=2) as executor:
                statuses = sorted(executor.map(send_once, range(2)))
        print(json.dumps({"statuses": statuses, "messages": len(mailer.sent)}))
    else:
        raise ValueError("invalid operation")
finally:
    db.close()
