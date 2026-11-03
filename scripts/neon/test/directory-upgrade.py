"""Exercise both release commands on the actual pre-0004 dump/restore from inventory-upgrade."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

sys.path.insert(0, "tests/postgres")
from test_migration_directory import manifest, sample  # noqa: E402

data = json.load(sys.stdin)
root = Path(data["root"])


def private(name, content):
    path = root / name
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
    path.chmod(0o600)
    return path


target = private(
    "destination", {"environment": "staging", "synthetic": True, "target": data["target"]["target"]}
)
source, evidence = sample()
owner = UUID(int=1)
pair = {"legacySubject": "synthetic-historical", "ownerId": str(owner)}
pairs = private(
    "pairs",
    {
        "version": 1,
        "environment": "staging",
        "assertion": "owner-approved-synthetic-fixtures-preserve-all-exclude-recovery",
        "approvalArtifactDigest": "1" * 64,
        "approvalRef": "fixture-review/1",
        "attestedBy": "synthetic-release",
        "attestedAt": datetime.now(UTC).isoformat(),
        "records": [pair],
    },
)
evidence["pairReviewDigest"] = hashlib.sha256(pairs.read_bytes()).hexdigest()
command = [
    sys.executable,
    "scripts/neon/import-legacy-directory.py",
    "--environment",
    "staging",
    "--target-file",
    str(target),
    "--source-file",
    str(private("export", source)),
    "--attestation-file",
    str(private("evidence", evidence)),
]


def run(*args, accepted=True):
    result = subprocess.run(
        [*command, *map(str, args)],
        capture_output=True,
        env={
            "PATH": os.environ["PATH"],
            "KENKUI_ENV": "staging",
            "KENKUI_STAGING_RELEASE_DATABASE_URL": data["url"],
        },
    )
    assert result.returncode == (0 if accepted else 1), result.stderr
    if accepted:
        return json.loads(result.stdout)
    assert result.stderr.strip() == b'{"accepted":0}'


run("--backfill-dry-run", "--pair-review-file", pairs)
run("--backfill-apply", "--pair-review-file", pairs)
account = str(UUID(int=2))
opening = {
    "accountId": account,
    "availableCredits": "4",
    "netLedgerCredits": "0",
    "openingResidualCredits": "4",
    "legacyLots": [
        {
            "lotId": "legacy:" + account,
            "reference": "legacy:" + account,
            "credited": "4",
            "reserved": "0",
            "consumed": "0",
        }
    ],
}
original = private("original", data["source"])
restored = private("restored", data["target"])
review = private("review", data["review"])
evidence.update(
    restoreReviewDigest=hashlib.sha256(review.read_bytes()).hexdigest(),
    originalSourceCaptureDigest=data["source"]["captureDigest"],
)
with psycopg.connect(data["url"], row_factory=dict_row, autocommit=True) as conn:

    def recapture():
        record = pair | {
            "fixtureFingerprint": fixture_fingerprint(conn, "staging", owner),
            "financePolicy": "preserve-approved-unknown-legacy",
            "legacyOpeningBalances": [opening],
        }
        private("manifest", manifest(conn, evidence, [record], version=2))
        private("evidence", evidence)

    recapture()
    common = ["--quarantine-file", root / "manifest"]
    verified = [
        "--original-inventory-file",
        original,
        "--restored-inventory-file",
        restored,
        "--restore-review-file",
        review,
    ]
    run("--dry-run", *common, accepted=False)
    run("--dry-run", *common, *verified)
    assert run("--activate", *common, *verified)["quarantined_owners"] == 1
    # A fresh target snapshot and exact pair approval cannot excuse a newly missing allocation.
    conn.execute(
        "UPDATE credit_authorizations SET finalized_at=clock_timestamp() WHERE status='released'"
    )
    recapture()
    run("--activate", *common, *verified, accepted=False)
    assert conn.execute("SELECT count(*) AS n FROM migration_directory_runs").fetchone()["n"] == 1
