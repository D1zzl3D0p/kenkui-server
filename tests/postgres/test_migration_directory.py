"""Directory activation uses the restored target and private release evidence."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from kenkui_server.storage.migration_directory import LEGACY_ISSUER, import_directory
from kenkui_server.storage.postgres_database import PostgresDatabase

URL = "postgresql://postgres@127.0.0.1:55438/postgres"
pytestmark = pytest.mark.skipif(not os.getenv("KENKUI_TEST_DATABASE_URL"), reason="local Postgres")


@pytest.fixture()
def database():
    assert os.environ["KENKUI_TEST_DATABASE_URL"] == URL
    schema = sql.Identifier("directory_test_" + uuid4().hex)
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        db = PostgresDatabase(URL, schema=schema.as_string(admin).strip('"'))
        try:
            db.migrate()
            yield db
        finally:
            db.close()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))


def test_activation_reconciles_all_owners_and_only_swaps_complete_runs(database):
    db = database
    owner = uuid4()
    db.execute(
        "INSERT INTO identities(id,workos_subject,email) VALUES (%s,'user_old','other@old.test')",
        (owner,),
    )
    db.execute(
        "INSERT INTO provider_identities(issuer,subject,identity_id) VALUES (%s,'user_old',%s)",
        (LEGACY_ISSUER, owner),
    )
    db.execute("INSERT INTO identities(id) VALUES (%s)", (uuid4(),))
    source, attestation = sample()
    with db.connection() as conn:
        result = import_directory(conn, "staging", source, attestation, activate=False)
        assert (result.restored_owners, result.source_only) == (1, 1)
        assert not db.execute("SELECT * FROM migration_directory_runs").fetchall()
        import_directory(conn, "staging", source, attestation, activate=True)
        pointer = db.execute("SELECT run_id FROM migration_directory_active").fetchone()
        for bad in [
            {"sourceDigest": "0" * 64},
            {"environment": "production"},
            {"complete": False},
            {"mode": "final"},
            {"invitedEmails": None},
            {"capturedAt": (datetime.now(UTC) - timedelta(days=2)).isoformat()},
        ]:
            with pytest.raises(ValueError):
                import_directory(conn, "staging", source, attestation | bad, activate=True)
        for rows in [[], [source_rows()[1]], source_rows() * 2]:
            raw, evidence = sample(rows)
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, evidence, activate=True)
        assert db.execute("SELECT run_id FROM migration_directory_active").fetchone() == pointer
        assert (
            db.execute(
                "SELECT count(*) AS n FROM migration_directory_rows WHERE owner_id IS NULL"
            ).fetchone()["n"]
            == 1
        )
        assert (
            db.execute("SELECT address_key FROM migration_directory_aliases").fetchone()[
                "address_key"
            ]
            == "other@old.test"
        )


def test_final_run_survives_rehearsal_window_and_cli_hides_private_input(database, tmp_path):
    db = database
    captured = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    source, evidence = sample(captured=captured)
    evidence.update(
        mode="final",
        finalDigest=evidence["sourceDigest"],
        finalWatermark=evidence["watermark"],
        admissionsFrozen=True,
        writesFrozen=True,
    )
    with db.connection() as conn:
        schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
    export = tmp_path / "source"
    attestation = tmp_path / "evidence"
    export.write_bytes(source)
    env = dict(
        os.environ,
        KENKUI_ENV="staging",
        KENKUI_STAGING_RELEASE_DATABASE_URL=URL,
        PGOPTIONS=f"-c search_path={schema}",
    )
    command = [
        sys.executable,
        "scripts/neon/import-legacy-directory.py",
        "--environment",
        "staging",
        "--source-file",
        str(export),
        "--attestation-file",
        str(attestation),
    ]
    for mode in ["--dry-run", "--activate"]:
        attestation.write_text(json.dumps(evidence))
        output = subprocess.run(
            [*command, mode], env=env, capture_output=True, text=True, check=True
        )
        assert json.loads(output.stdout) == {
            "digest": evidence["sourceDigest"],
            "restored_owners": 0,
            "source_only": 2,
        }
    assert (
        db.execute("SELECT valid_until FROM migration_directory_runs").fetchone()["valid_until"]
        is None
    )
    for bad in [
        {"finalWatermark": "different"},
        {"mode": "provisional"},
        {"sourceClientId": "client_other"},
        {"invitedEmails": None},
        {"expectedSourceCount": 1},
        {"writesFrozen": False},
    ]:
        attestation.write_text(json.dumps(evidence | bad))
        output = subprocess.run([*command, "--activate"], env=env, capture_output=True, text=True)
        assert (
            output.returncode == 1
            and output.stderr.strip() == '{"accepted":0}'
            and output.stdout == ""
        )


def test_runtime_grants_prevent_directory_activation(database):
    from psycopg.errors import InsufficientPrivilege

    with database.connection() as conn:
        role = sql.Identifier("migration_runtime_" + uuid4().hex)
        with conn.transaction(force_rollback=True):
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
            schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), role)
            )
            grants = Path("deploy/neon/migration-runtime-grants.sql").read_text()
            for statement in "\n".join(
                line for line in grants.splitlines() if not line.startswith("--")
            ).split(";"):
                if statement.strip() and "neon_auth" not in statement:
                    conn.execute(
                        statement.replace(':"api_role"', role.as_string(conn)).replace(
                            ':"worker_role"', role.as_string(conn)
                        )
                    )
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(role))
            conn.execute("SELECT * FROM migration_directory_rows")
            conn.execute(
                "INSERT INTO migration_rate_events(kind,rate_key) VALUES ('issue','synthetic')"
            )
            with pytest.raises(InsufficientPrivilege), conn.transaction():
                conn.execute("DELETE FROM migration_directory_active")
            source, evidence = sample()
            with pytest.raises(ValueError):
                import_directory(conn, "staging", source, evidence, activate=True)


def test_prune_removes_only_expired_entries_within_a_shared_budget(database):
    from kenkui_server.storage.migration_challenges import prune_migration_recovery

    with database.connection() as conn:
        conn.execute("""INSERT INTO migration_rate_events(kind,rate_key,created_at)
                        SELECT 'issue','test',clock_timestamp()-interval '25 hours'
                        FROM generate_series(1,4)""")
        conn.execute("INSERT INTO migration_rate_events(kind,rate_key) VALUES ('verify','recent')")
        assert prune_migration_recovery(conn, batch_size=3) == 3
        assert prune_migration_recovery(conn, batch_size=3) == 1
        assert prune_migration_recovery(conn) == 0
        assert (
            conn.execute("SELECT rate_key FROM migration_rate_events").fetchone()["rate_key"]
            == "recent"
        )


def source_rows():
    return [
        {
            "id": "user_old",
            "email": "Old@Original.test",
            "emailVerifiedAt": "2026-01-01T00:00:00Z",
            "banned": False,
        },
        {"id": "user_only", "email": "new@source.test", "emailVerifiedAt": None, "banned": False},
    ]


def sample(rows=None, captured=None):
    rows = source_rows() if rows is None else rows
    captured = captured or datetime.now(UTC).isoformat()
    raw = json.dumps(
        {
            "authority": {
                "environmentId": LEGACY_ISSUER.split(":")[-2],
                "clientId": LEGACY_ISSUER.split(":")[-1],
            },
            "capturedAt": captured,
            "source": "fixture",
            "result": {
                "userlandUsers": {"data": rows, "listMetadata": {"before": None, "after": None}}
            },
        }
    ).encode()
    return raw, {
        "environment": "staging",
        "sourceEnvironmentId": LEGACY_ISSUER.split(":")[-2],
        "sourceClientId": LEGACY_ISSUER.split(":")[-1],
        "sourceDigest": hashlib.sha256(raw).hexdigest(),
        "expectedSourceCount": len(rows),
        "capturedAt": captured,
        "watermark": "snapshot-1",
        "mode": "provisional",
        "invitedEmails": [],
        "complete": True,
    }
