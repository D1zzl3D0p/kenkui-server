"""Directory activation uses the restored target and private release evidence."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

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
            "matched_restored_owners": 0,
            "quarantined_owners": 0,
            "quarantine_digest": None,
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


def test_activation_expiring_while_inserting_rows_preserves_usable_pointer(database):
    with database.connection() as conn:
        source, evidence = sample()
        import_directory(conn, "staging", source, evidence, activate=True)
        previous = conn.execute("SELECT run_id FROM migration_directory_active").fetchone()
        conn.execute("""CREATE FUNCTION wait_until_expiry() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_sleep_until(valid_until) FROM migration_directory_runs
            WHERE id=NEW.run_id; RETURN NEW; END $$""")
        conn.execute("""CREATE TRIGGER pause_activation AFTER INSERT ON migration_directory_rows
            FOR EACH ROW EXECUTE FUNCTION wait_until_expiry()""")
        captured = (datetime.now(UTC) - timedelta(hours=24) + timedelta(seconds=1)).isoformat()
        source, evidence = sample(captured=captured)
        with pytest.raises(ValueError):
            import_directory(conn, "staging", source, evidence, activate=True)
        assert (
            conn.execute("""SELECT a.run_id FROM migration_directory_active a
            JOIN migration_directory_runs r ON r.id=a.run_id
            WHERE r.valid_until>clock_timestamp()""").fetchone()
            == previous
        )
        assert conn.execute("SELECT count(*) n FROM migration_directory_runs").fetchone()["n"] == 1


def test_runtime_grants_prevent_directory_activation(database):
    from psycopg.errors import InsufficientPrivilege

    with database.connection() as conn:
        api = sql.Identifier("migration_api_" + uuid4().hex)
        worker = sql.Identifier("migration_worker_" + uuid4().hex)
        with conn.transaction(force_rollback=True):
            schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
            for role in (api, worker):
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), role)
                )
            # Simulate privileges materialized by preexisting release-owner defaults.
            conn.execute(
                sql.SQL("GRANT ALL ON migration_directory_quarantines TO {},{},PUBLIC").format(
                    api, worker
                )
            )
            conn.execute(
                sql.SQL("GRANT SELECT ON migration_directory_runs TO {},{},PUBLIC").format(
                    api, worker
                )
            )
            conn.execute(
                sql.SQL("GRANT SELECT (approval_ref) ON migration_directory_runs TO {}").format(api)
            )
            raw, ev = sample()
            import_directory(conn, "staging", raw, ev, activate=True)
            grants = Path("deploy/neon/migration-runtime-grants.sql").read_text()
            statements = "\n".join(
                line for line in grants.splitlines() if not line.startswith("--")
            ).split(";")

            def apply_grants():
                for statement in statements:
                    if statement.strip() and "neon_auth" not in statement:
                        for key, role in [("api_role", api), ("worker_role", worker)]:
                            statement = statement.replace(
                                f':"{key}"', role.as_string(conn)
                            ).replace(
                                f":'{key}'",
                                sql.Literal(role.as_string(conn).strip('"')).as_string(conn),
                            )
                        conn.execute(statement)

            inherited = sql.Identifier("migration_reader_" + uuid4().hex)
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(inherited))
            conn.execute(
                sql.SQL(
                    "GRANT SELECT (legacy_finance_evidence) ON "
                    "migration_directory_quarantines TO {}"
                ).format(inherited)
            )
            conn.execute(sql.SQL("GRANT {} TO {}").format(inherited, worker))
            with pytest.raises(psycopg.errors.DivisionByZero), conn.transaction():
                apply_grants()
            conn.execute(sql.SQL("REVOKE {} FROM {}").format(inherited, worker))
            apply_grants()

            # A session can assume these roles even when none of their privileges
            # are automatically inherited. Exercise the access as the runtime session.
            bridge = sql.Identifier("migration_bridge_" + uuid4().hex)
            writer = sql.Identifier("migration_writer_" + uuid4().hex)
            for group in (bridge, writer):
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(group))
            for group in (inherited, writer):
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), group)
                )
            conn.execute(sql.SQL("GRANT DELETE ON migration_directory_active TO {}").format(writer))
            switchable = sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE")
            conn.execute(switchable.format(bridge, api))
            apply_grants()  # The intermediate role has no private reads or directory writes.
            for runtime, via, target, operation in (
                (
                    worker,
                    worker,
                    inherited,
                    "SELECT legacy_finance_evidence FROM migration_directory_quarantines",
                ),
                (api, bridge, writer, "DELETE FROM migration_directory_active WHERE false"),
                (worker, worker, api, "SELECT invited_emails FROM migration_directory_runs"),
            ):
                with conn.transaction(force_rollback=True):
                    conn.execute(switchable.format(target, via))
                    with conn.transaction(force_rollback=True):
                        conn.execute(sql.SQL("SET LOCAL SESSION AUTHORIZATION {}").format(runtime))
                        with pytest.raises(InsufficientPrivilege), conn.transaction():
                            conn.execute(operation)
                        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(target))
                        conn.execute(operation)
                    with pytest.raises(psycopg.errors.DivisionByZero), conn.transaction():
                        apply_grants()
            apply_grants()
            insert_event = "INSERT INTO migration_rate_events(kind,rate_key) VALUES ('issue','key')"
            insert_challenge = (
                "INSERT INTO migration_challenges SELECT * FROM migration_challenges WHERE false"
            )
            for role in (api, worker):
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(role))
                conn.execute("SELECT * FROM migration_directory_rows")
                if role == api:
                    ready = conn.execute("""SELECT r.id,r.issuer,r.invited_emails
                        FROM migration_directory_active a JOIN migration_directory_runs r
                        ON r.id=a.run_id AND r.environment=a.environment
                        WHERE a.environment='staging'
                        AND (r.mode='final' AND r.frozen OR r.valid_until>clock_timestamp())""")
                else:
                    ready = conn.execute("""SELECT d.issuer FROM migration_directory_active a
                        JOIN migration_directory_runs d ON d.id=a.run_id
                        WHERE a.environment='staging'
                        AND (d.mode='final' AND d.frozen OR d.valid_until>clock_timestamp())""")
                assert ready.fetchone()["issuer"] == LEGACY_ISSUER
                for column in (
                    "approval_ref",
                    "attested_by",
                    "approval_artifact_digest",
                    "attested_at",
                    "restored_snapshot_digest",
                    "quarantine_digest",
                    "quarantine_version",
                    "matched_restored_owners",
                    "quarantined_owners",
                    "digest",
                ):
                    with pytest.raises(InsufficientPrivilege), conn.transaction():
                        conn.execute(
                            sql.SQL("SELECT {} FROM migration_directory_runs").format(
                                sql.Identifier(column)
                            )
                        )
                with pytest.raises(InsufficientPrivilege), conn.transaction():
                    conn.execute("DELETE FROM migration_directory_active")
                for denied in (
                    "SELECT * FROM migration_directory_quarantines",
                    "SELECT finance_policy FROM migration_directory_quarantines",
                    "SELECT legacy_finance_evidence FROM migration_directory_quarantines",
                    "DELETE FROM migration_directory_quarantines",
                    "UPDATE migration_directory_runs SET quarantined_owners=0",
                ):
                    with pytest.raises(InsufficientPrivilege), conn.transaction():
                        conn.execute(denied)
                source, evidence = sample()
                with pytest.raises(ValueError):
                    import_directory(conn, "staging", source, evidence, activate=True)
                if role == api:
                    conn.execute(insert_event)
                    conn.execute(insert_challenge)
                    with pytest.raises(InsufficientPrivilege), conn.transaction():
                        conn.execute("DELETE FROM migration_rate_events")
                else:
                    conn.execute("UPDATE migration_challenges SET claimed_at=clock_timestamp()")
                    conn.execute("DELETE FROM migration_rate_events")
                    with pytest.raises(InsufficientPrivilege), conn.transaction():
                        conn.execute(insert_event)
                    with pytest.raises(InsufficientPrivilege), conn.transaction():
                        conn.execute(insert_challenge)
                conn.execute("RESET ROLE")


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


@pytest.mark.parametrize("fail_prune", [False, True])
def test_daily_maintenance_prunes_one_bounded_batch_and_preserves_release_evidence(
    database, monkeypatch, fail_prune
):
    from types import SimpleNamespace

    from psycopg.pq import TransactionStatus

    from kenkui_server import hosted, maintenance

    raw, evidence = sample(captured=(datetime.now(UTC) - timedelta(days=3)).isoformat())
    evidence.update(
        mode="final",
        finalDigest=evidence["sourceDigest"],
        finalWatermark=evidence["watermark"],
        admissionsFrozen=True,
        writesFrozen=True,
    )
    tables = (
        "migration_directory_runs",
        "migration_directory_active",
        "migration_directory_rows",
        "migration_directory_aliases",
        "migration_directory_quarantines",
    )
    with database.connection() as conn:
        owner = seed_owner(conn, 10, "user_old")
        records = fixture_records(conn, "staging", 1)
        quarantine = manifest(conn, evidence, records)
        import_directory(conn, "staging", raw, evidence, activate=True, quarantine=quarantine)
        before = {table: conn.execute("SELECT * FROM " + table).fetchall() for table in tables}
        run = conn.execute("SELECT run_id FROM migration_directory_active").fetchone()["run_id"]
        conn.execute(
            """INSERT INTO migration_challenges
               (id,run_id,issuer,subject,old_subject,owner_id,digest,expires_at)
               SELECT gen_random_uuid(),%s,'fixture','new','user_old',%s,%s,
               clock_timestamp()-interval '1 second' FROM generate_series(1,101)""",
            (run, owner, bytes(32)),
        )
        conn.execute(
            """INSERT INTO migration_challenges
               (id,run_id,issuer,subject,old_subject,owner_id,digest,expires_at)
               VALUES (gen_random_uuid(),%s,'fixture','live','user_old',%s,%s,
                       clock_timestamp()+interval '1 hour')""",
            (run, owner, bytes(32)),
        )
        conn.execute(
            """INSERT INTO migration_rate_events(kind,rate_key,created_at)
               SELECT 'issue','old',clock_timestamp()-interval '25 hours'
               FROM generate_series(1,4)"""
        )
        conn.execute("INSERT INTO migration_rate_events(kind,rate_key) VALUES ('verify','recent')")
        conn.execute(
            """INSERT INTO source_upload_intents
               (source_id,owner_id,sha256,expires_at,next_cleanup_at)
               VALUES ('unsubmitted-source',%s,repeat('a',64),
                       now()-interval '1 day',now()-interval '1 day')""",
            (owner,),
        )
        conn.execute(
            """INSERT INTO assets(id,owner_id,path,sha256,format,created_at,deleted_at)
               VALUES ('active-source',%s,'src',repeat('a',64),'epub',now()-interval '2 days',NULL),
                      ('late-source',%s,'src',repeat('a',64),'epub',now()-interval '2 days',
                       now()-interval '2 days')""",
            (owner, owner),
        )
        conn.execute(
            """INSERT INTO jobs VALUES
               ('active-job','{"source_id":"active-source"}','running',1,'{}',%s,NULL)""",
            (owner,),
        )

    def outside_transaction(*args, **kwargs):
        with database.connection() as conn:
            assert conn.info.transaction_status == TransactionStatus.IDLE

    backups = []
    deleted_sources = []

    def delete_source(source_id):
        outside_transaction()
        assert getattr(database._state, "connection", None) is None
        deleted_sources.append(source_id)

    def backup(*args, **kwargs):
        outside_transaction()
        backups.append(True)
        # Exactly one shared batch of 100, not a loop draining the queue.
        assert database.execute("SELECT count(*) n FROM migration_challenges").fetchone()["n"] == 2
        assert database.execute("SELECT count(*) n FROM migration_rate_events").fetchone()["n"] == 5
        with database.connection() as conn:
            assert before == {
                table: conn.execute("SELECT * FROM " + table).fetchall() for table in tables
            }

    monkeypatch.setenv("KENKUI_DATABASE_IDLE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://worker:private@db-pooler.example/db")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://backup:private@db.example/db")
    monkeypatch.setenv(
        "KENKUI_BACKUP_AGE_RECIPIENT",
        "age1wdwys0mm9w3px2w23av5edn6prdy5d80axvu3m52tnu9g08f4u4svmn5mx",
    )
    monkeypatch.setenv("KENKUI_DEPLOYMENT", "staging")
    monkeypatch.setenv("R2_BUCKET", "fixture")
    monkeypatch.setattr(hosted, "_recovery_database", lambda: database)
    monkeypatch.setattr(hosted, "_run_recovery", outside_transaction)
    monkeypatch.setattr(
        hosted, "object_store", lambda: SimpleNamespace(delete_source=delete_source)
    )
    monkeypatch.setattr(hosted, "r2_client", lambda: object())
    monkeypatch.setattr(
        hosted,
        "recovery_signals",
        lambda: SimpleNamespace(list_records=lambda: (), arm=outside_transaction),
    )
    monkeypatch.setattr(maintenance, "write_logical_backup", backup)

    def age_probe(command, **kwargs):
        assert command[:3] == ["age", "--encrypt", "--recipient"]
        assert kwargs["input"] == b""

    # The external CLI has its own real full-roundtrip test; this test isolates daily SQL.
    monkeypatch.setattr(maintenance.subprocess, "run", age_probe)
    # Keep the borrowed fixture open to verify failure; teardown restores close().
    monkeypatch.setattr(database, "close", lambda: None)
    if fail_prune:
        database.execute("""CREATE FUNCTION fail_prune() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic prune failure'; END $$""")
        database.execute("""CREATE TRIGGER fail_prune AFTER DELETE ON migration_challenges
            FOR EACH STATEMENT EXECUTE FUNCTION fail_prune()""")
        with pytest.raises(psycopg.errors.RaiseException, match="synthetic prune failure"):
            hosted.retain_objects()
        assert backups == []
        assert (
            database.execute("SELECT count(*) n FROM migration_challenges").fetchone()["n"] == 102
        )
        assert database.execute("SELECT count(*) n FROM migration_rate_events").fetchone()["n"] == 5
        outside_transaction()
    else:
        hosted.retain_objects()
        assert backups == [True]
    assert sorted(deleted_sources) == ["late-source", "unsubmitted-source"]
    assert (
        database.execute("SELECT deleted_at FROM assets WHERE id='active-source'").fetchone()[
            "deleted_at"
        ]
        is None
    )
    assert database.execute(
        "SELECT cleanup_started FROM source_upload_intents WHERE source_id='unsubmitted-source'"
    ).fetchone()["cleanup_started"]


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


def seed_owner(conn, n, subject):
    owner = UUID(int=n)
    conn.execute("INSERT INTO identities(id,workos_subject) VALUES (%s,%s)", (owner, subject))
    conn.execute(
        "INSERT INTO provider_identities(issuer,subject,identity_id) VALUES (%s,%s,%s)",
        (LEGACY_ISSUER, subject, owner),
    )
    return owner


def manifest(conn, evidence, records, *, approval_ref="fixture-review/1", version=1):
    from kenkui_server.storage.migration_fixture_quarantine import restored_snapshot_digest

    snapshot = restored_snapshot_digest(conn)
    payload = {
        k: evidence[k]
        for k in ("environment", "sourceEnvironmentId", "sourceClientId", "sourceDigest")
    }
    payload.update(
        version=version,
        restoredSnapshotDigest=snapshot,
        approvalArtifactDigest="1" * 64,
        approvalRef=approval_ref,
        attestedBy="synthetic-release",
        attestedAt=datetime.now(UTC).isoformat(),
        assertion="owner-approved-synthetic-fixtures-preserve-all-exclude-recovery",
        records=records,
    )
    raw = json.dumps(payload).encode()
    evidence.update(
        restoredSnapshotDigest=snapshot,
        quarantineApproval={
            k: payload[k]
            for k in (
                "approvalArtifactDigest",
                "approvalRef",
                "attestedBy",
                "attestedAt",
                "records",
            )
        },
        quarantineManifestDigest=hashlib.sha256(raw).hexdigest(),
    )
    if evidence["mode"] == "final":
        evidence["finalRestoredSnapshotDigest"] = snapshot
    return raw


def fixture_records(conn, env, count):
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    records = []
    for i in range(count):
        subject = f"user_fixture{i}"
        owner = seed_owner(conn, 100 + i, subject)
        conn.execute(
            "INSERT INTO jobs VALUES (%s,%s,'succeeded',1,'{}',%s,NULL)",
            (f"job-{i}", '{"source_id":"book","title":"synthetic"}', owner),
        )
        conn.execute(
            "INSERT INTO credit_accounts(id,identity_id,available_credits) VALUES (%s,%s,17)",
            (UUID(int=200 + i), owner),
        )
        account = UUID(int=200 + i)
        conn.execute(
            "INSERT INTO assets(id,owner_id,path,sha256,format) "
            "VALUES (%s,%s,'sample.epub',%s,'epub')",
            (f"book-{i}", owner, "a" * 64),
        )
        conn.execute(
            "INSERT INTO artifacts VALUES (%s,%s,'sample.m4b','m4b')", (f"artifact-{i}", f"job-{i}")
        )
        conn.execute(
            """INSERT INTO credit_lots(id,account_id,kind,reference,credited)
               VALUES (%s,%s,'grant',%s,17)""",
            (f"grant-{i}", account, f"beta-allowance:{owner}"),
        )
        conn.execute(
            """INSERT INTO credit_ledger_entries(id,account_id,kind,credits,reference)
                        VALUES (%s,%s,'purchase',17,%s)""",
            (UUID(int=300 + i), account, f"beta-allowance:{owner}"),
        )
        conn.execute(
            """INSERT INTO credit_authorizations(id,job_id,account_id,credits,status)
                        VALUES (%s,%s,%s,3,'released')""",
            (UUID(int=400 + i), f"job-{i}", account),
        )
        conn.execute(
            "INSERT INTO credit_allocations VALUES (%s,%s,%s,3)",
            (UUID(int=400 + i), f"grant-{i}", account),
        )
        records.append(
            {
                "ownerId": str(owner),
                "legacySubject": subject,
                "fixtureFingerprint": fixture_fingerprint(conn, env, owner),
            }
        )
    return records


def legacy_finance_records(conn):
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    records = fixture_records(conn, "staging", 3)
    for i, (balance, net, residual) in enumerate([(4, -3, 7), (0, -23, 23)]):
        account = UUID(int=200 + i)
        lot_id = f"legacy:{account}"
        conn.execute("DELETE FROM credit_allocations WHERE account_id=%s", (account,))
        conn.execute("DELETE FROM credit_lots WHERE account_id=%s", (account,))
        conn.execute("DELETE FROM credit_ledger_entries WHERE account_id=%s", (account,))
        conn.execute(
            "UPDATE credit_accounts SET available_credits=%s WHERE id=%s", (balance, account)
        )
        conn.execute(
            """INSERT INTO credit_lots(id,account_id,kind,reference,credited)
                        VALUES(%s,%s,'legacy',%s,%s)""",
            (lot_id, account, lot_id, balance),
        )
        # The reservations were finalized before 0004 captured current availability.
        settled = UUID(int=500 + i)
        conn.execute(
            """INSERT INTO jobs(id,spec_json,status,version,progress_json,owner_id)
                    SELECT %s,spec_json,'succeeded',1,progress_json,owner_id
                    FROM jobs WHERE id=%s""",
            (f"settled-{i}", f"job-{i}"),
        )
        conn.execute(
            """INSERT INTO credit_authorizations(id,job_id,account_id,credits,status)
                        VALUES(%s,%s,%s,%s,'settled')""",
            (settled, f"settled-{i}", account, -net),
        )
        released = UUID(int=400 + i)
        for auth, kind, credits in [
            (settled, "reservation", net),
            (settled, "settlement", 0),
            (released, "reservation", -3),
            (released, "release", 3),
        ]:
            conn.execute(
                """INSERT INTO credit_ledger_entries
                       (id,account_id,authorization_id,kind,credits,reference)
                    VALUES(gen_random_uuid(),%s,%s,%s,%s,%s)""",
                (account, auth, kind, credits, f"{kind}:{auth}"),
            )
        records[i].update(
            fixtureFingerprint=fixture_fingerprint(conn, "staging", UUID(int=100 + i)),
            financePolicy="preserve-approved-unknown-legacy",
            legacyOpeningBalances=[
                dict(
                    accountId=str(account),
                    availableCredits=str(balance),
                    netLedgerCredits=str(net),
                    openingResidualCredits=str(residual),
                    legacyLots=[
                        dict(
                            lotId=lot_id,
                            reference=lot_id,
                            credited=str(balance),
                            reserved="0",
                            consumed="0",
                        )
                    ],
                )
            ],
        )
    records[2].update(financePolicy="strict-v1", legacyOpeningBalances=[])
    return records


def test_exact_v2_signed_legacy_finance_preserved_and_retained(database):
    from kenkui_server.storage.migration_challenges import prune_migration_recovery

    with database.connection() as conn:
        records = legacy_finance_records(conn)
        before = owned_inventory(conn)
        raw, ev = sample()
        strict = [
            {k: v for k, v in r.items() if k not in ("financePolicy", "legacyOpeningBalances")}
            for r in records
        ]
        v1 = manifest(conn, ev, strict)
        with pytest.raises(ValueError):
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=v1)
        for final in (False, True):
            if final:
                conn.execute("""INSERT INTO stored_objects(id,object_kind,opaque_object_key,
                         resource_id,terminal_at) VALUES(gen_random_uuid(),'temporary',
                         'unrelated-key','unrelated-resource',now())""")
                raw, ev = sample(source_rows() + [source_rows()[0] | {"id": "user_fresh"}])
                ev.update(
                    mode="final",
                    finalDigest=ev["sourceDigest"],
                    finalWatermark=ev["watermark"],
                    admissionsFrozen=True,
                    writesFrozen=True,
                )
            q = manifest(conn, ev, records, version=2)
            for activate in (False, True):
                result = import_directory(conn, "staging", raw, ev, activate=activate, quarantine=q)
                assert result.quarantined_owners == 3
                assert result.source_only == ev["expectedSourceCount"]
        conn.execute("""INSERT INTO migration_rate_events(kind,rate_key,created_at)
                        VALUES ('issue','test',now()-interval '2 days')""")
        assert prune_migration_recovery(conn) == 1
        rows = conn.execute("""SELECT owner_id, finance_policy, legacy_finance_evidence,
                                      quarantine_version
                               FROM migration_directory_quarantines q
                               JOIN migration_directory_runs r ON r.id=q.run_id
                               ORDER BY owner_id""").fetchall()
        digests = conn.execute("""SELECT DISTINCT restored_snapshot_digest
                                 FROM migration_directory_runs""").fetchall()
        assert len(digests) == 2
        assert len(rows) == 6
        for row, record in zip(rows, [r for r in records for _ in range(2)], strict=True):
            assert row["quarantine_version"] == 2
            assert str(row["owner_id"]) == record["ownerId"]
            assert row["finance_policy"] == record["financePolicy"]
            assert row["legacy_finance_evidence"] == (record["legacyOpeningBalances"] or None)
        assert owned_inventory(conn) == before


def test_v2_conserves_mixed_legacy_and_grant_availability(database):
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    with database.connection() as conn:
        records = legacy_finance_records(conn)
        account = UUID(int=200)
        # A later matched grant adds 3 credits after the existing reservation
        # consumed 3 legacy credits. The combined lot availability remains 4.
        conn.execute("UPDATE credit_lots SET consumed=3 WHERE account_id=%s", (account,))
        conn.execute(
            """INSERT INTO credit_lots(id,account_id,kind,reference,credited)
                        VALUES('grant-later',%s,'grant','fictional-grant',3)""",
            (account,),
        )
        conn.execute(
            """INSERT INTO credit_ledger_entries(id,account_id,kind,credits,reference)
                        VALUES(gen_random_uuid(),%s,'purchase',3,'fictional-grant')""",
            (account,),
        )
        opening = records[0]["legacyOpeningBalances"][0]
        opening.update(netLedgerCredits="0", openingResidualCredits="4")
        opening["legacyLots"][0]["consumed"] = "3"
        records[0]["fixtureFingerprint"] = fixture_fingerprint(conn, "staging", UUID(int=100))
        raw, ev = sample()
        q = manifest(conn, ev, records, version=2)
        import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)


def test_v2_rejects_inexact_diagnostic_and_independent_approval(database):
    from copy import deepcopy

    with database.connection() as conn:
        raw, ev = sample()
        records = legacy_finance_records(conn)
        for patch in [
            {"accountId": records[1]["legacyOpeningBalances"][0]["accountId"]},
            {"availableCredits": "04"},
            {"availableCredits": 4},
            {"availableCredits": True},
            {"netLedgerCredits": "-0"},
            {"netLedgerCredits": "-4"},
            {"openingResidualCredits": "4"},
            {"openingResidualCredits": "-1"},
            {"legacyLots": []},
            {"newField": None},
        ]:
            bad = deepcopy(records)
            bad[0]["legacyOpeningBalances"][0].update(patch)
            q = manifest(conn, ev, bad, version=2)
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        for edit in (
            lambda r: r.pop("financePolicy"),
            lambda r: r.update(financePolicy="preserve"),
            lambda r: r.update(financePolicy="strict-v1"),
            lambda r: r.update(legacyOpeningBalances=[]),
            lambda r: r["legacyOpeningBalances"].extend(r["legacyOpeningBalances"][:]),
            lambda r: r["legacyOpeningBalances"][0]["legacyLots"].extend(
                r["legacyOpeningBalances"][0]["legacyLots"][:]
            ),
            lambda r: r["legacyOpeningBalances"][0]["legacyLots"][0].update(reference="changed"),
        ):
            bad = deepcopy(records)
            edit(bad[0])
            q = manifest(conn, ev, bad, version=2)
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, ev, activate=False, quarantine=q)
        q = manifest(conn, ev, records, version=2)
        ev["quarantineApproval"]["records"] = deepcopy(records)
        ev["quarantineApproval"]["records"][0]["legacyOpeningBalances"][0][
            "openingResidualCredits"
        ] = "4"
        with pytest.raises(ValueError):
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        assert not conn.execute("SELECT * FROM migration_directory_runs").fetchall()


def test_v2_exceptions_cannot_excuse_purchase_ownership_or_unrelated_corruption(database):
    from copy import deepcopy

    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    with database.connection() as conn:
        records = legacy_finance_records(conn)
        raw, ev = sample()
        for statement in [
            "UPDATE credit_lots SET kind='purchase' WHERE credited=4",
            "UPDATE credit_ledger_entries SET reference='stripe:fixture' WHERE credits=-23",
            "INSERT INTO payment_events SELECT 'legacy', account_id::text, now() "
            "FROM credit_lots WHERE credited=4",
            "UPDATE credit_ledger_entries SET kind='purchase' WHERE credits=-23",
            "UPDATE credit_lots SET reference='unmatched-grant' WHERE kind='grant'",
            "UPDATE credit_authorizations SET account_id=("
            "SELECT id FROM credit_accounts WHERE available_credits=0) WHERE job_id='job-0'",
            "UPDATE credit_accounts SET available_credits=0 WHERE available_credits=4",
            "UPDATE credit_ledger_entries SET credits=0 WHERE kind='purchase'",
            "INSERT INTO provider_identities SELECT 'neon','claimant',id,now() "
            "FROM identities WHERE workos_subject='user_fixture0'",
            """INSERT INTO identity_link_proofs
                      (id,identity_id,issuer,subject,verification_method,expires_at)
               SELECT gen_random_uuid(),id,'neon','claimant','legacy_session',now()+interval '1 day'
               FROM identities WHERE workos_subject='user_fixture0'""",
            "UPDATE credit_lots SET id='renamed',reference='renamed' WHERE credited=4",
        ]:
            with conn.transaction(force_rollback=True):
                conn.execute(statement)
                changed = deepcopy(records)
                for r in changed:
                    r["fixtureFingerprint"] = fixture_fingerprint(
                        conn, "staging", UUID(r["ownerId"])
                    )
                opening = changed[0]["legacyOpeningBalances"][0]
                if statement.startswith("UPDATE credit_accounts"):
                    opening.update(availableCredits="0", openingResidualCredits="3")
                if "'renamed'" in statement:
                    opening["legacyLots"][0].update(lotId="renamed", reference="renamed")
                q = manifest(conn, ev, changed, version=2)
                with pytest.raises(ValueError):
                    import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        # Both sides of the composite allocation FKs must belong to the same account.
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute("""UPDATE credit_allocations SET lot_id=(
                         SELECT id FROM credit_lots WHERE credited=4)""")
        assert not conn.execute("SELECT * FROM migration_directory_active").fetchall()


def test_v2_changed_finance_cannot_reuse_or_replay_approval(database):
    from copy import deepcopy

    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    with database.connection() as conn:
        raw, ev = sample()
        original = legacy_finance_records(conn)
        q = manifest(conn, ev, original, version=2)
        import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        # A reused review reference identifies the same privately retained artifact bytes.
        changed_approval = json.loads(q) | {"approvalArtifactDigest": "a" * 64}
        changed_bytes = json.dumps(changed_approval).encode()
        other = ev | {
            "quarantineManifestDigest": hashlib.sha256(changed_bytes).hexdigest(),
            "quarantineApproval": ev["quarantineApproval"] | {"approvalArtifactDigest": "a" * 64},
        }
        with pytest.raises(ValueError):
            import_directory(conn, "staging", raw, other, activate=True, quarantine=changed_bytes)
        # Neither stale evidence nor a recomputed inventory under the same old
        # approval authorizes this changed account.
        conn.execute("UPDATE credit_accounts SET available_credits=0 WHERE available_credits=4")
        conn.execute("UPDATE credit_lots SET credited=0 WHERE credited=4")
        changed = deepcopy(original)
        changed[0]["fixtureFingerprint"] = fixture_fingerprint(conn, "staging", UUID(int=100))
        opening = changed[0]["legacyOpeningBalances"][0]
        opening.update(availableCredits="0", openingResidualCredits="3")
        opening["legacyLots"][0]["credited"] = "0"
        for records in (original, changed):
            q = manifest(conn, ev, records, version=2)
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        renewed = manifest(conn, ev, changed, version=2, approval_ref="fixture-review/2")
        import_directory(conn, "staging", raw, ev, activate=True, quarantine=renewed)
        conn.execute("UPDATE credit_accounts SET available_credits=4 WHERE id=%s", (UUID(int=200),))
        conn.execute("UPDATE credit_lots SET credited=4 WHERE account_id=%s", (UUID(int=200),))
        q = manifest(conn, ev, original, version=2)
        with pytest.raises(ValueError):
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)


@pytest.mark.parametrize(
    ("env", "matched", "quarantined"), [("staging", 1, 2), ("production", 4, 1)]
)
def test_exact_fixture_partition(database, env, matched, quarantined):
    from kenkui_server.storage.migration_fixture_quarantine import restored_snapshot_digest

    rows = [
        source_rows()[0] | {"id": f"user_s{i}", "email": f"s{i}@example.test"} for i in range(4)
    ]
    raw, evidence = sample(rows)
    evidence["environment"] = env
    with database.connection() as conn:
        for i in range(matched):
            seed_owner(conn, 10 + i, rows[i]["id"])
        records = fixture_records(conn, env, quarantined)
        q = manifest(conn, evidence, records)
        with pytest.raises(ValueError):
            import_directory(conn, env, raw, evidence, activate=True)
        owned = owned_inventory(conn)
        before = restored_snapshot_digest(conn)
        for active in (False, True):
            result = import_directory(conn, env, raw, evidence, activate=active, quarantine=q)
            assert (
                result.restored_owners,
                result.matched_restored_owners,
                result.quarantined_owners,
                result.source_only,
            ) == (matched + quarantined, matched, quarantined, 4 - matched)
            assert result.quarantine_digest == hashlib.sha256(q).hexdigest()
            assert restored_snapshot_digest(conn) == before
            assert conn.execute("SELECT count(*) n FROM migration_directory_runs").fetchone()[
                "n"
            ] == int(active)
        # Independent full-row copy, including fields deliberately absent from the fingerprint.
        assert owned == owned_inventory(conn)
        if env == "staging":
            assert (
                records[0]["fixtureFingerprint"]
                == "a27cae64a15a8b59ab171c853ed8c66a50374fc9a4ce62e0ed2bfca7daf1a8f7"
            )
        assert (
            conn.execute("SELECT count(*) n FROM migration_directory_quarantines").fetchone()["n"]
            == quarantined
        )
        assert not conn.execute("""
                SELECT 1 FROM migration_directory_quarantines q JOIN migration_directory_rows r
                ON q.run_id=r.run_id AND (q.subject=r.subject OR q.owner_id=r.owner_id)
                """).fetchall()


def owned_inventory(conn):
    tables = (
        "identities",
        "provider_identities",
        "jobs",
        "assets",
        "artifacts",
        "credit_accounts",
        "credit_authorizations",
        "credit_ledger_entries",
        "credit_lots",
        "credit_allocations",
        "payment_events",
    )
    return [
        conn.execute(
            sql.SQL("SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)").format(
                sql.Identifier(table)
            )
        ).fetchall()
        for table in tables
    ]


def test_reject_fixture_manifest_and_classification_drift(database):
    raw, evidence = sample()
    with database.connection() as conn:
        import_directory(conn, "staging", raw, evidence, activate=True)
        records = fixture_records(conn, "staging", 1)
        q = manifest(conn, evidence, records)

        def rejected(data, ev):
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, ev, activate=True, quarantine=data)
            assert (
                conn.execute("SELECT count(*) n FROM migration_directory_runs").fetchone()["n"] == 1
            )
            assert not conn.execute("SELECT * FROM migration_directory_quarantines").fetchall()

        for patch in [
            {"environment": "production"},
            {"sourceClientId": "x"},
            {"version": True},
            {"approvalRef": ""},
            {"extra": 1},
            {"records": []},
            {"records": records * 2},
            {"records": [records[0] | {"legacySubject": "user_old"}]},
            {"records": [records[0] | {"ownerId": str(UUID(int=777))}]},
            {"attestedAt": "2099-01-01T00:00:00Z"},
            {"restoredSnapshotDigest": "f" * 64},
        ]:
            bad = json.dumps(json.loads(q) | patch).encode()
            rejected(bad, evidence | {"quarantineManifestDigest": hashlib.sha256(bad).hexdigest()})
        rejected(q, evidence | {"quarantineApproval": None})
        for entries in [
            records * 2,
            [],
            [records[0] | {"legacySubject": "user_old"}],
            [records[0] | {"ownerId": str(UUID(int=333))}],
        ]:
            current = evidence.copy()
            rejected(manifest(conn, current, entries), current)
        for command in [
            "UPDATE identities SET email='fixture@example.test'",
            "UPDATE jobs SET status='pending'",
            "UPDATE jobs SET spec_json='{}'",
            "UPDATE credit_accounts SET available_credits=18",
            "INSERT INTO credit_lots(id,account_id,kind,reference,credited) "
            "SELECT 'p',id,'purchase','stripe:test',10 FROM credit_accounts",
            "UPDATE identities SET workos_subject='user_other'",
            "DELETE FROM provider_identities",
            "UPDATE credit_allocations SET credits=1",
            "UPDATE credit_lots SET reference='unknown'",
            "UPDATE assets SET path='other'",
            "INSERT INTO payment_events SELECT 'beta-allowance',id::text,now() FROM identities",
            "INSERT INTO jobs SELECT 'extra',spec_json,"
            "status,version,progress_json,owner_id,terminal_at "
            "FROM jobs",
            """INSERT INTO migration_challenges(id,run_id,issuer,subject,old_subject,owner_id,
                                              digest,expires_at)
               SELECT gen_random_uuid(),run_id,'neon','new','user_old',i.id,
                      decode(repeat('00',32),'hex'),
                      now()+interval '1 hour' FROM migration_directory_active,identities i""",
            "INSERT INTO provider_identities SELECT 'neon','new',id,now() FROM identities",
            """INSERT INTO identity_link_proofs(id,identity_id,issuer,subject,
                                               verification_method,expires_at)
               SELECT gen_random_uuid(),id,'neon','new','legacy_session',
                      now()+interval '1 hour' FROM identities""",
        ]:
            with conn.transaction(force_rollback=True):
                conn.execute(command)
                # Fresh full target evidence cannot replace the approved fixture classification.
                current = evidence.copy()
                rejected(manifest(conn, current, records), current)


def test_fixture_approval_survives_recapture_but_final_target_drift_requires_reattestation(
    database,
):
    from kenkui_server.storage.migration_challenges import prune_migration_recovery
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    with database.connection() as conn:
        records = fixture_records(conn, "staging", 1)
        old_capture = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        raw, ev = sample(captured=old_capture)

        def finalized(evidence):
            return evidence | dict(
                mode="final",
                finalDigest=evidence["sourceDigest"],
                finalWatermark=evidence["watermark"],
                admissionsFrozen=True,
                writesFrozen=True,
            )

        ev = finalized(ev)
        old_manifest = manifest(conn, ev, records)
        import_directory(conn, "staging", raw, ev, activate=True, quarantine=old_manifest)
        prior = conn.execute("SELECT run_id FROM migration_directory_active").fetchone()["run_id"]
        conn.execute("UPDATE jobs SET terminal_at=now()")
        seed_owner(conn, 999, "user_added")
        refreshed, fresh = sample(
            source_rows() + [source_rows()[0] | {"id": "user_added"}], captured=old_capture
        )
        fresh = finalized(fresh)
        assert (
            fixture_fingerprint(conn, "staging", UUID(int=100)) == records[0]["fixtureFingerprint"]
        )
        for data, evidence in [(raw, ev), (refreshed, fresh)]:
            with pytest.raises(ValueError):
                import_directory(
                    conn, "staging", data, evidence, activate=True, quarantine=old_manifest
                )
        update = manifest(conn, fresh, records)
        for change in [
            {"finalRestoredSnapshotDigest": ev["finalRestoredSnapshotDigest"]},
            {"quarantineApproval": ev["quarantineApproval"]},
            {"writesFrozen": False},
        ]:
            with pytest.raises(ValueError):
                import_directory(
                    conn, "staging", refreshed, fresh | change, activate=True, quarantine=update
                )
        assert (
            conn.execute("SELECT run_id FROM migration_directory_active").fetchone()["run_id"]
            == prior
        )
        import_directory(conn, "staging", refreshed, fresh, activate=True, quarantine=update)
        conn.execute(
            "INSERT INTO migration_rate_events(kind,rate_key,created_at) "
            "VALUES ('issue','test',now()-interval '2 days')"
        )
        assert prune_migration_recovery(conn) == 1
        for table, n in [
            ("migration_directory_quarantines", 2),
            ("migration_directory_runs", 2),
            ("migration_directory_active", 1),
            ("migration_directory_rows", 5),
        ]:
            assert (
                conn.execute(
                    sql.SQL("SELECT count(*) n FROM {}").format(sql.Identifier(table))
                ).fetchone()["n"]
                == n
            )
        assert (
            conn.execute("SELECT run_id FROM migration_directory_active").fetchone()["run_id"]
            != prior
        )


def test_fixture_locks_block_child_writes_and_failure_rolls_back_every_row(database):
    raw, ev = sample()
    with database.connection() as conn:
        import_directory(conn, "staging", raw, ev, activate=True)
        prior = conn.execute("SELECT * FROM migration_directory_active").fetchall()
        records = legacy_finance_records(conn)
        q = manifest(conn, ev, records, version=2)
        conn.execute("""CREATE FUNCTION fail_fixture() RETURNS trigger LANGUAGE plpgsql AS $$
             BEGIN RAISE EXCEPTION 'injected'; END $$""")
        conn.execute("""CREATE TRIGGER fail_fixture AFTER INSERT ON migration_directory_quarantines
             FOR EACH ROW EXECUTE FUNCTION fail_fixture()""")
        with pytest.raises(psycopg.errors.RaiseException):
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        assert conn.execute("SELECT * FROM migration_directory_active").fetchall() == prior
        assert conn.execute("SELECT count(*) n FROM migration_directory_runs").fetchone()["n"] == 1
        assert not conn.execute("SELECT * FROM migration_directory_quarantines").fetchall()
        conn.execute("DROP TRIGGER fail_fixture ON migration_directory_quarantines")
        with conn.transaction(), psycopg.connect(URL, autocommit=True) as writer:
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
            schema = conn.execute("SELECT current_schema() AS name").fetchone()["name"]
            writer.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            writer.execute("SET lock_timeout='100ms'")
            for cmd in [
                "UPDATE jobs SET status='failed'",
                "UPDATE credit_accounts SET available_credits=0",
                "UPDATE assets SET path=path",
                "UPDATE payment_events SET provider=provider",
            ]:
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    writer.execute(cmd)


def test_unreviewed_or_unsafe_inventory_cannot_be_approved_by_updating_only_digests(database):
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    with database.connection() as conn:
        raw, evidence = sample()
        import_directory(conn, "staging", raw, evidence, activate=True)
        records = fixture_records(conn, "staging", 1)
        owner = UUID(records[0]["ownerId"])
        for statement in [
            "UPDATE identities SET email='present@test.invalid'",
            "UPDATE jobs SET status='running'",
            "UPDATE credit_lots SET kind='purchase'",
            "UPDATE credit_ledger_entries SET reference='stripe:purchase'",
            "UPDATE credit_lots SET credited=18",
            "INSERT INTO payment_events SELECT 'beta-allowance',id::text,now() FROM identities",
            "INSERT INTO provider_identities SELECT 'other','subject',id,now() FROM identities",
            """INSERT INTO identity_link_proofs(id,identity_id,issuer,subject,
                                               verification_method,expires_at)
               SELECT gen_random_uuid(),id,'test','s','legacy_session',
                      now()+interval '1 hour' FROM identities""",
        ]:
            with conn.transaction(force_rollback=True):
                conn.execute(statement)
                ev = evidence.copy()
                record = records[0] | {
                    "fixtureFingerprint": fixture_fingerprint(conn, "staging", owner)
                }
                q = manifest(conn, ev, [record])
                for activate in (False, True):
                    with pytest.raises(ValueError):
                        import_directory(conn, "staging", raw, ev, activate=activate, quarantine=q)
        for transfer in [
            "UPDATE jobs SET owner_id=%s",
            "UPDATE assets SET owner_id=%s",
            "UPDATE provider_identities SET identity_id=%s",
            "UPDATE credit_accounts SET identity_id=%s",
        ]:
            with conn.transaction(force_rollback=True):
                other = uuid4()
                conn.execute("INSERT INTO identities(id) VALUES (%s)", (other,))
                conn.execute(transfer, (other,))
                ev = evidence.copy()
                q = manifest(conn, ev, records)
                with pytest.raises(ValueError):
                    import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        # Authenticated overlap and an extra missing owner must both abort.
        for overlap in (True, False):
            with conn.transaction(force_rollback=True):
                rows = source_rows()
                if overlap:
                    rows.append(rows[0] | {"id": "user_fixture0"})
                else:
                    seed_owner(conn, 998, "user_unreviewed")
                new, ev = sample(rows)
                q = manifest(conn, ev, records)
                with pytest.raises(ValueError):
                    import_directory(conn, "staging", new, ev, activate=True, quarantine=q)
        assert conn.execute("SELECT count(*) n FROM migration_directory_runs").fetchone()["n"] == 1


def test_quarantine_cli_requires_independent_evidence_and_hides_records(database, tmp_path):
    raw, evidence = sample()
    with database.connection() as conn:
        schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
        q = manifest(conn, evidence, fixture_records(conn, "staging", 1))
    source, attestation, quarantine = [
        tmp_path / n for n in ("source", "attestation", "quarantine")
    ]
    source.write_bytes(raw)
    attestation.write_text(json.dumps(evidence))
    quarantine.write_bytes(q)
    command = [
        sys.executable,
        "scripts/neon/import-legacy-directory.py",
        "--environment",
        "staging",
        "--source-file",
        str(source),
        "--attestation-file",
        str(attestation),
        "--quarantine-file",
        str(quarantine),
    ]
    env = dict(
        os.environ,
        KENKUI_ENV="staging",
        KENKUI_STAGING_RELEASE_DATABASE_URL=URL,
        PGOPTIONS=f"-c search_path={schema}",
    )
    out = subprocess.run(
        [*command, "--dry-run"], env=env, text=True, capture_output=True, check=True
    )
    assert json.loads(out.stdout) == dict(
        restored_owners=1,
        matched_restored_owners=0,
        quarantined_owners=1,
        source_only=2,
        digest=evidence["sourceDigest"],
        quarantine_digest=evidence["quarantineManifestDigest"],
    )
    quarantine.write_bytes(q + b" ")
    out = subprocess.run([*command, "--activate"], env=env, text=True, capture_output=True)
    assert (out.returncode, out.stdout, out.stderr.strip()) == (1, "", '{"accepted":0}')
    assert not database.execute("SELECT * FROM migration_directory_active").fetchall()


def test_quarantine_expiring_behind_child_writer_preserves_pointer(database):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    with database.connection() as conn:
        raw, ev = sample()
        import_directory(conn, "staging", raw, ev, activate=True)
        before = conn.execute("SELECT * FROM migration_directory_active").fetchall()
        records = fixture_records(conn, "staging", 1)
        expires = datetime.now(UTC) + timedelta(seconds=1.5)
        raw, ev = sample(captured=(expires - timedelta(days=1)).isoformat())
        q = manifest(conn, ev, records)
        with database.connection() as contender, ThreadPoolExecutor(max_workers=1) as pool:
            with conn.transaction():
                conn.execute("LOCK TABLE credit_lots IN ROW EXCLUSIVE MODE")
                future = pool.submit(
                    import_directory, contender, "staging", raw, ev, activate=True, quarantine=q
                )
                deadline = monotonic() + 4
                while not conn.execute(
                    "SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted",
                    (contender.info.backend_pid,),
                ).fetchone():
                    assert monotonic() < deadline
                    sleep(0.01)
                conn.execute("SELECT pg_sleep_until(%s)", (expires,))
            with pytest.raises(ValueError):
                future.result(timeout=5)
        assert conn.execute("SELECT * FROM migration_directory_active").fetchall() == before
        assert conn.execute("SELECT count(*) n FROM migration_directory_runs").fetchone()["n"] == 1
        assert not conn.execute("SELECT * FROM migration_directory_quarantines").fetchall()


def test_upgrades_backfill_old_runs_and_strict_v1_quarantine_rows(tmp_path):
    from psycopg.rows import dict_row

    schema = "quarantine_upgrade_" + uuid4().hex
    with psycopg.connect(URL, autocommit=True, row_factory=dict_row) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        db = PostgresDatabase(URL, schema=schema)
        try:
            for path in Path("migrations/postgresql").glob("*.sql"):
                if path.name < "0009":
                    (tmp_path / path.name).symlink_to(path.resolve())
            db.migrate(tmp_path)
            db.execute("""INSERT INTO migration_directory_runs(id,environment,issuer,captured_at,
                watermark,digest,mode,frozen,invited_emails,restored_owners,source_only)
                VALUES(gen_random_uuid(),'staging','legacy',now(),'mark',repeat('0',64),
                       'final',true,'{}',3,2)""")
            previous = Path("migrations/postgresql/0009_migration_fixture_quarantine.sql")
            (tmp_path / previous.name).symlink_to(previous.resolve())
            db.migrate(tmp_path)
            fixture_run = uuid4()
            with db.connection() as conn:
                owner = seed_owner(conn, 101, "user_upgrade")
                conn.execute(
                    """INSERT INTO migration_directory_runs(
                    id,environment,issuer,captured_at,watermark,digest,mode,frozen,invited_emails,
                    restored_owners,source_only,matched_restored_owners,quarantined_owners,
                    quarantine_version,quarantine_digest,approval_artifact_digest,approval_ref,
                    attested_by,attested_at,restored_snapshot_digest)
                    VALUES(%s,'staging','legacy',now(),'mark',repeat('0',64),'final',true,'{}',
                        1,0,0,1,1,repeat('1',64),repeat('2',64),'review','operator',now(),
                        repeat('3',64))""",
                    (fixture_run,),
                )
                conn.execute(
                    """INSERT INTO migration_directory_quarantines
                    VALUES(%s,%s,'user_upgrade',repeat('4',64))""",
                    (fixture_run, owner),
                )
            db.migrate()
            assert db.execute("""SELECT finance_policy,legacy_finance_evidence
                               FROM migration_directory_quarantines""").fetchone() == {
                "finance_policy": "strict-v1",
                "legacy_finance_evidence": None,
            }
            with db.connection() as conn:
                for policy, value in [
                    ("unknown", None),
                    ("strict-v1", "[]"),
                    ("preserve-approved-unknown-legacy", None),
                    ("preserve-approved-unknown-legacy", "[]"),
                    ("preserve-approved-unknown-legacy", "{}"),
                ]:
                    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                        conn.execute(
                            """UPDATE migration_directory_quarantines
                            SET finance_policy=%s,legacy_finance_evidence=%s::jsonb""",
                            (policy, value),
                        )
            assert db.execute("""SELECT restored_owners,matched_restored_owners,quarantined_owners,
                    quarantine_digest FROM migration_directory_runs
                    WHERE quarantine_version IS NULL""").fetchone() == {
                "restored_owners": 3,
                "matched_restored_owners": 3,
                "quarantined_owners": 0,
                "quarantine_digest": None,
            }
        finally:
            db.close()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_release_history_prevents_omission_source_reappearance_or_silent_reclassification(database):
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    raw, ev = sample()
    with database.connection() as conn:
        records = fixture_records(conn, "staging", 1)
        q = manifest(conn, ev, records)
        import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        previous = conn.execute("SELECT * FROM migration_directory_active").fetchall()
        reappeared, new = sample(source_rows() + [source_rows()[0] | {"id": "user_fixture0"}])
        with pytest.raises(ValueError):
            import_directory(conn, "staging", reappeared, new, activate=True)
        with conn.transaction(force_rollback=True):
            conn.execute("DELETE FROM migration_directory_active")
            with pytest.raises(ValueError):
                import_directory(conn, "staging", reappeared, new, activate=True)
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE identities SET workos_subject=NULL")
            conn.execute("DELETE FROM provider_identities")
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, ev, activate=True)
        conn.execute("UPDATE jobs SET status='cancelled'")
        changed = [
            records[0] | {"fixtureFingerprint": fixture_fingerprint(conn, "staging", UUID(int=100))}
        ]
        q = manifest(conn, ev, changed)
        with pytest.raises(ValueError):
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        assert conn.execute("SELECT * FROM migration_directory_active").fetchall() == previous
        assert conn.execute("SELECT count(*) n FROM migration_directory_runs").fetchone()["n"] == 1
        # Exact renewed owner classification has a fresh durable approval reference.
        q = manifest(conn, ev, changed, approval_ref="fixture-review/2")
        import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
        with conn.transaction(force_rollback=True):
            conn.execute("DELETE FROM migration_directory_active")
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)
            renewed = manifest(conn, ev, changed, approval_ref="fixture-review/3")
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=renewed)
        # Returning to an old fingerprint is another change, even if its old approval is replayed.
        conn.execute("UPDATE jobs SET status='succeeded'")
        q = manifest(conn, ev, records)
        with pytest.raises(ValueError):
            import_directory(conn, "staging", raw, ev, activate=True, quarantine=q)


@pytest.mark.parametrize(
    "case", ["legacy", "no-lots", "unexplained", "no-grant-ledger", "extra-ledger"]
)
def test_uncorroborated_fixture_credits_fail_first_capture(database, case):
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    with database.connection() as conn:
        raw, ev = sample()
        records = fixture_records(conn, "staging", 1)
        if case == "unexplained":
            conn.execute("UPDATE credit_accounts SET available_credits=18")
        elif case == "extra-ledger":
            conn.execute("""INSERT INTO credit_ledger_entries(id,account_id,kind,credits,reference)
                            SELECT gen_random_uuid(),id,'release',2,'unexplained'
                            FROM credit_accounts""")
        else:
            conn.execute("DELETE FROM credit_ledger_entries")
            if case == "legacy":
                conn.execute("UPDATE credit_lots SET kind='legacy',reference='old-carryover'")
            elif case == "no-lots":
                conn.execute("DELETE FROM credit_allocations")
                conn.execute("DELETE FROM credit_lots")
        records[0]["fixtureFingerprint"] = fixture_fingerprint(conn, "staging", UUID(int=100))
        q = manifest(conn, ev, records)
        for activate in (False, True):
            with pytest.raises(ValueError):
                import_directory(conn, "staging", raw, ev, activate=activate, quarantine=q)
        assert not conn.execute("SELECT * FROM migration_directory_runs").fetchall()


def test_selective_object_ownership_uses_kind_and_resource(database):
    from kenkui_server.storage.migration_fixture_quarantine import fixture_fingerprint

    with database.connection() as conn:
        records = fixture_records(conn, "staging", 1)

        def insert(kind, resource):
            conn.execute(
                """INSERT INTO stored_objects(id,object_kind,opaque_object_key,
                        resource_id,terminal_at) VALUES (%s,%s,%s,%s,now())""",
                (uuid4(), kind, uuid4().hex, resource),
            )

        for kind, resource in [
            ("artifact", "book-0"),
            ("source", "sample.m4b"),
            ("temporary", "book-0"),
        ]:
            insert(kind, resource)
        before = records[0]["fixtureFingerprint"]
        assert fixture_fingerprint(conn, "staging", UUID(int=100)) == before
        for kind, resource in [("source", "book-0"), ("artifact", "sample.m4b")]:
            insert(kind, resource)
            current = fixture_fingerprint(conn, "staging", UUID(int=100))
            assert current != before
            before = current
