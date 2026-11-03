# ruff: noqa: E501
"""Bounded synthetic PG17 locale rehearsal; never accepts an operator database URL.

Run from the repository root with .venv/bin/python scripts/neon/test/paired-locale.py.
Requires the already-pulled pinned image, Docker, OpenSSL, and installed JS dependencies.
Only the two fixed containers below are created; all data and TLS material are transient.
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid4

import psycopg

from kenkui_server.jobs.models import (
    Dispatch,
    Job,
    JobSpec,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.storage.migration_directory import mailbox
from kenkui_server.storage.postgres import (
    PostgresDispatchRepository,
    PostgresHostedRepository,
    PostgresIdentityRepository,
    PostgresJobRepository,
)
from kenkui_server.storage.postgres_database import PostgresDatabase
from kenkui_server.storage.source_uploads import PostgresSourceUploads

ROOT = Path(__file__).resolve().parents[3]
IMAGE = "postgres@sha256:839fbb5a4942a7758dd93e18b42ea5649807491a0545b5eeeb8f0b8bb11857a5"
LABEL = "kenkui.synthetic-locale-rehearsal=20261103"
OWNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ACCOUNT = "33333333-3333-4333-8333-333333333333"
VARIANTS = ["Key", "key", "é", "e\u0301"]
CANONICAL = [
    "00000000-0000-4000-8000-000000000009",
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-00000000000a",
    "00000000-0000-4000-8000-000000000010",
]
IRREGULAR = ["a", "A", "a-", "a_", "é", "e\u0301", "Z", "z", "!", "１"]
MAILBOXES = {
    " User.Name+Tag@Example.INVALID ": "user.name+tag@example.invalid",
    "o'hara!a%test@Sub-Domain.example.invalid": "o'hara!a%test@sub-domain.example.invalid",
    "Üser@example.invalid": None,
    "user@éxample.invalid": None,
    "İ@example.invalid": None,
    "user@example.invalid\n": None,
    "\tuser@example.invalid": None,
    "user..name@example.invalid": None,
    "user@-example.invalid": None,
}


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=120)


def emit(kind: str, **data: object) -> None:
    print(json.dumps({"kind": kind, **data}, ensure_ascii=False, default=str), flush=True)


def expect_state(db: PostgresDatabase, statement: str, values: tuple, state: str) -> None:
    try:
        with db.transaction():
            db.execute(statement, values)
    except psycopg.Error as error:
        assert error.sqlstate == state, (error.sqlstate, state)
    else:
        raise AssertionError(f"expected SQLSTATE {state}: {statement}")


def identity(db: PostgresDatabase) -> dict:
    row = db.execute("""SELECT current_setting('server_version') AS version,
        current_setting('server_version_num')::integer / 10000 AS major,
        pg_encoding_to_char(encoding) AS encoding, datlocprovider AS provider,
        datcollate, datctype, datlocale, datcollversion AS stored,
        pg_database_collation_actual_version(oid) AS actual
        FROM pg_database WHERE datname=current_database()""").fetchone()
    assert row["major"] == 17 and row["encoding"] == "UTF8", row
    return row


def catalogs(db: PostgresDatabase) -> dict:
    return {
        "migrations": db.execute(
            'SELECT name FROM schema_migrations ORDER BY name COLLATE "C"'
        ).fetchall(),
        "indexes": db.execute("""SELECT c.relname AS name, i.indisunique, i.indisvalid,
            i.indisready, pg_get_indexdef(i.indexrelid) AS definition,
            ARRAY(SELECT co.collname FROM unnest(i.indcollation) WITH ORDINALITY u(oid,n)
                JOIN pg_collation co ON co.oid=u.oid ORDER BY n) AS collations,
            ARRAY(SELECT op.opcname FROM unnest(i.indclass) WITH ORDINALITY u(oid,n)
                JOIN pg_opclass op ON op.oid=u.oid ORDER BY n) AS operator_classes
            FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() ORDER BY c.relname COLLATE "C"\n            """).fetchall(),
        "text_columns": db.execute("""SELECT c.relname AS table_name,a.attname AS column_name,
            co.collname AS collation FROM pg_attribute a
            JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_collation co ON co.oid=a.attcollation
            WHERE n.nspname=current_schema() AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname COLLATE "C",a.attname COLLATE "C"\n            """).fetchall(),
        "checks": db.execute("""SELECT c.relname AS table_name,con.conname,
            pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con
            JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND con.contype='c'
            ORDER BY c.relname COLLATE "C",con.conname COLLATE "C"\n            """).fetchall(),
    }


def digest_checks(db: PostgresDatabase, phase: str) -> None:
    valid = "0123456789abcdef" * 4
    cases = {
        "valid": valid,
        "short": valid[:-1],
        "long": valid + "0",
        "uppercase": "A" + valid[1:],
        "punctuation": ":" + valid[1:],
        "newline": valid + "\n",
        "accented": "á" + valid[1:],
        "fullwidth": "ａ" + valid[1:],
        "arabic_digit": "١" + valid[1:],
    }
    run = str(uuid4())
    base = (run, valid)
    db.execute(
        """INSERT INTO migration_directory_runs
        (id,environment,issuer,captured_at,watermark,digest,mode,frozen,invited_emails,
         restored_owners,source_only,matched_restored_owners)
        VALUES(%s,'staging','synthetic',now(),'synthetic',%s,'final',true,'{}',1,0,1)""",
        base,
    )
    statements = {
        "0007.source_upload_intents.sha256": (
            "INSERT INTO source_upload_intents(source_id,owner_id,sha256) VALUES(%s,%s,%s)",
            lambda value: ("digest_" + uuid4().hex, OWNER, value),
        ),
        "0008.migration_directory_runs.digest": (
            "UPDATE migration_directory_runs SET digest=%s WHERE id=%s",
            lambda value: (value, run),
        ),
        "0009.migration_directory_quarantines.fixture_fingerprint": (
            "INSERT INTO migration_directory_quarantines(run_id,owner_id,subject,fixture_fingerprint) VALUES(%s,%s,%s,%s)",
            lambda value: (run, OTHER, "digest", value),
        ),
    }
    for column in ("quarantine_digest", "approval_artifact_digest", "restored_snapshot_digest"):
        fields = {
            name: valid
            for name in (
                "quarantine_digest",
                "approval_artifact_digest",
                "restored_snapshot_digest",
            )
        }

        def parameters(value: str, *, name: str = column, fields: dict = fields) -> tuple:
            copy = {**fields, name: value}
            return (*copy.values(), run)

        statements[f"{phase}.migration_directory_runs.{column}"] = (
            """UPDATE migration_directory_runs SET quarantined_owners=1,
            matched_restored_owners=0,quarantine_version=1,
            quarantine_digest=%s,approval_artifact_digest=%s,restored_snapshot_digest=%s,
            approval_ref='synthetic',attested_by='synthetic',attested_at=now() WHERE id=%s""",
            parameters,
        )
    for name, (sql, params) in statements.items():
        for case, value in cases.items():
            # Roll back even positive inserts so every attempt has a valid, identical context.
            with db.transaction():
                if case == "valid":
                    db.execute(sql, params(value))
                    state = "00000"
                else:
                    expect_state(db, sql, params(value), "23514")
                    state = "23514"
                emit("digest", phase=phase, constraint=name, case=case, sqlstate=state)
                raise_rollback = db.execute("SELECT 1").fetchone()
                assert raise_rollback is not None
                # psycopg's forced rollback avoids persistent fixture changes.
                raise psycopg.Rollback()
    db.execute("DELETE FROM migration_directory_runs WHERE id=%s", (run,))


def key_checks(db: PostgresDatabase) -> None:
    identities = PostgresIdentityRepository(db)
    owners = [str(identities.user_id_for_subject(value)) for value in VARIANTS]
    assert len(set(owners)) == len(VARIANTS)
    for value, owner in zip(VARIANTS, owners, strict=True):
        assert str(identities.user_id_for_subject(value, "synthetic@example.invalid")) == owner
        expect_state(
            db,
            "INSERT INTO identities(id,workos_subject) VALUES(%s,%s)",
            (str(uuid4()), value),
            "23505",
        )
    statements = {
        "assets.id": (
            "INSERT INTO assets(id,path,sha256,format,owner_id) VALUES(%s,'synthetic',%s,'txt',%s)",
            lambda value: (value, "a" * 64, OWNER),
        ),
        "provider_identities.issuer_subject": (
            "INSERT INTO provider_identities(issuer,subject,identity_id) VALUES('synthetic',%s,%s)",
            lambda value: (value, OTHER),
        ),
        "job_idempotency.key": (
            "INSERT INTO job_idempotency(key,job_id) VALUES(%s,%s)",
            lambda value: (value, CANONICAL[0]),
        ),
        "payment_events.provider_event": (
            "INSERT INTO payment_events VALUES('synthetic',%s,now())",
            lambda value: (value,),
        ),
        "stored_objects.opaque_object_key": (
            "INSERT INTO stored_objects(id,object_kind,opaque_object_key,terminal_at) VALUES(%s,'source',%s,now())",
            lambda value: (str(uuid4()), value),
        ),
        "stored_objects.kind_resource": (
            "INSERT INTO stored_objects(id,object_kind,opaque_object_key,terminal_at,resource_id) VALUES(%s,'artifact',%s,now(),%s)",
            lambda value: (str(uuid4()), "resource_" + uuid4().hex, value),
        ),
        "credit_ledger_entries.reference": (
            "INSERT INTO credit_ledger_entries(id,account_id,kind,credits,reference) VALUES(%s,%s,'purchase',0,%s)",
            lambda value: (str(uuid4()), ACCOUNT, value),
        ),
    }
    for name, (sql, params) in statements.items():
        for value in VARIANTS:
            db.execute(sql, params(value))
            expect_state(db, sql, params(value), "23505")
        emit("keys", key=name, accepted=VARIANTS, exact_duplicate_sqlstate="23505")
    for value in VARIANTS:
        assert (
            str(
                db.execute("SELECT owner_id FROM assets WHERE id=%s", (value,)).fetchone()[
                    "owner_id"
                ]
            )
            == OWNER
        )
        assert (
            str(
                db.execute(
                    "SELECT identity_id FROM provider_identities WHERE issuer='synthetic' AND subject=%s",
                    (value,),
                ).fetchone()["identity_id"]
            )
            == OTHER
        )
        assert (
            db.execute(
                "SELECT resource_id FROM stored_objects WHERE object_kind='artifact' AND resource_id=%s",
                (value,),
            ).fetchone()["resource_id"]
            == value
        )
    assert db.execute("SELECT 1 FROM assets WHERE id='KEY'").fetchone() is None
    emit("identity", distinct_owner_count=len(owners), exact_upserts=True, lookup_owners=True)


def seed_jobs(db: PostgresDatabase, ids: list[str]) -> dict:
    spec = JobSpec(
        "synthetic",
        ("chapter",),
        SingleVoiceCasting("voice"),
        TtsSettings(),
        OutputSpec("book.m4b"),
    )
    repo = PostgresJobRepository(db)
    dispatches = PostgresDispatchRepository(db)
    # Valid production encoders; owner is filled by the hosted production admission path below.
    from kenkui_server.storage.repositories import _encode_progress, _encode_spec

    for index, value in enumerate(ids):
        job = Job(value, spec)
        db.execute(
            """INSERT INTO jobs(id,spec_json,status,version,progress_json,owner_id)
            VALUES(%s,%s,'queued',0,%s,%s)""",
            (
                value,
                _encode_spec(spec),
                _encode_progress(job.progress),
                OWNER if index % 2 == 0 else OTHER,
            ),
        )
        dispatches.create(Dispatch(value, value, "pending"))
    result = {
        "jobs": [job.id for job in repo.list()],
        "pending": [item.id for item in dispatches.list_pending()],
        "incomplete": [item.id for item in dispatches.list_incomplete()],
    }
    assert set(result["jobs"]) == set(ids)
    assert result["jobs"] == result["pending"] == result["incomplete"]
    assert all(
        str(repo.owner_id(value)) == (OWNER if index % 2 == 0 else OTHER)
        for index, value in enumerate(ids)
    )
    return result


def cleanup_checks(db: PostgresDatabase, ids: list[str]) -> dict:
    db.execute("DELETE FROM source_upload_intents")
    for index, value in enumerate(ids):
        if index % 2:
            db.execute(
                """INSERT INTO assets(id,path,sha256,format,owner_id,deleted_at)
                VALUES(%s,'synthetic',%s,'txt',%s,'2000-01-01Z')""",
                ("cleanup_" + value, "a" * 64, OWNER),
            )
        else:
            db.execute(
                """INSERT INTO source_upload_intents(source_id,owner_id,sha256,expires_at,next_cleanup_at)
                VALUES(%s,%s,%s,'2000-01-01Z','2000-01-01Z')""",
                ("cleanup_" + value, OWNER, "a" * 64),
            )
    # A due intent for a completed live asset is fenced and must never delete that asset.
    live = "cleanup_live"
    db.execute(
        "INSERT INTO assets(id,path,sha256,format,owner_id) VALUES(%s,'live',%s,'txt',%s)",
        (live, "a" * 64, OWNER),
    )
    db.execute(
        """INSERT INTO source_upload_intents(source_id,owner_id,sha256,expires_at,next_cleanup_at)
        VALUES(%s,%s,%s,'2000-01-01Z','2000-01-01Z')""",
        (live, OWNER, "a" * 64),
    )

    class Objects:
        def __init__(self):
            self.attempts = []
            self.fail_once = True

        def delete_source(self, value):
            self.attempts.append(value)
            if self.fail_once:
                self.fail_once = False
                raise OSError("synthetic first delete failure")

    objects = Objects()
    cleanup = PostgresSourceUploads(db)
    batches = []
    succeeded = 0
    for _ in range(len(ids) + 2):
        before = len(objects.attempts)
        count = cleanup.reap(objects, limit=2)
        succeeded += count
        batches.append(objects.attempts[before:])
        if not objects.attempts[before:] and count == 0:
            break
    expected = {"cleanup_" + value for value in ids}
    assert set(objects.attempts) == expected and len(objects.attempts) == len(ids)
    assert succeeded == len(ids) - 1 and live not in objects.attempts
    failed = objects.attempts[0]
    assert cleanup.reap(objects, limit=2) == 0
    db.execute(
        "UPDATE source_upload_intents SET next_cleanup_at='2000-01-01Z' WHERE source_id=%s",
        (failed,),
    )
    db.execute("UPDATE assets SET next_source_cleanup_at='2000-01-01Z' WHERE id=%s", (failed,))
    assert cleanup.reap(objects, limit=2) == 1 and objects.attempts[-1] == failed
    assert cleanup.reap(objects, limit=2) == 0
    assert (
        db.execute("SELECT 1 FROM source_upload_intents WHERE source_id=%s", (live,)).fetchone()
        is None
    )
    assert (
        db.execute("SELECT deleted_at FROM assets WHERE id=%s", (live,)).fetchone()["deleted_at"]
        is None
    )
    db.execute("DELETE FROM assets WHERE id LIKE 'cleanup_%'")
    db.execute("DELETE FROM source_upload_intents")
    return {
        "batches": batches,
        "failed_then_retried": failed,
        "attempts": objects.attempts,
        "live_asset_preserved": True,
        "eligible_count": len(ids),
    }


def prepare_typescript(admin: PostgresDatabase, url: str) -> None:
    admin.execute("CREATE SCHEMA locale_ts")
    db = PostgresDatabase(url, schema="locale_ts")
    try:
        db.migrate()
        for owner in (OWNER, OTHER):
            db.execute(
                "INSERT INTO identities(id,workos_subject) VALUES(%s,%s)",
                (owner, "synthetic_" + owner),
            )
        for account, owner in ((ACCOUNT, OWNER), ("44444444-4444-4444-8444-444444444444", OTHER)):
            db.execute(
                "INSERT INTO credit_accounts(id,identity_id) VALUES(%s,%s)", (account, owner)
            )
            PostgresHostedRepository(db).billing.process_payment_event(
                "stripe", "synthetic-ts-" + account, account, 100
            )
        for source, owner in (("owned-source", OWNER), ("foreign-source", OTHER)):
            db.execute(
                "INSERT INTO assets(id,path,sha256,format,owner_id) VALUES(%s,'synthetic',%s,'txt',%s)",
                (source, "a" * 64, owner),
            )
    finally:
        db.close()


def exercise(url: str, side: str) -> dict:
    admin = PostgresDatabase(url)
    try:
        actual = identity(admin)
        expected = {
            "source": ("c", "en_US.UTF8", "2.36", None),
            "target": ("b", "C.UTF-8", "1", "C.UTF-8"),
        }[side]
        assert (
            actual["provider"],
            actual["datcollate"],
            actual["stored"],
            actual["datlocale"],
        ) == expected, actual
        assert actual["datctype"] == expected[1] and actual["actual"] == expected[2], actual
        emit("database", side=side, **actual)
        admin.execute("CREATE SCHEMA locale_rehearsal")
        db = PostgresDatabase(url, schema="locale_rehearsal")
        try:
            # Test 0009's original complete CHECK before 0010 replaces it.
            with tempfile.TemporaryDirectory(prefix="locale-migrations-") as folder:
                for path in (ROOT / "migrations/postgresql").glob("*.sql"):
                    if path.name < "0010":
                        (Path(folder) / path.name).write_text(path.read_text())
                db.migrate(Path(folder))
            for owner in (OWNER, OTHER):
                db.execute(
                    "INSERT INTO identities(id,workos_subject) VALUES(%s,%s)",
                    (owner, "synthetic_" + owner),
                )
            digest_checks(db, "0009")
            db.migrate()
            db.migrate()
            digest_checks(db, "0010")
            catalog = catalogs(db)
            assert all(row["indisvalid"] and row["indisready"] for row in catalog["indexes"])
            assert all(row["collation"] == "default" for row in catalog["text_columns"])
            emit("catalog", side=side, **catalog)
            db.execute(
                "INSERT INTO credit_accounts(id,identity_id) VALUES(%s,%s)", (ACCOUNT, OWNER)
            )
            canonical = seed_jobs(db, CANONICAL)
            assert canonical["jobs"] == sorted(CANONICAL)
            emit("ordering", side=side, contract="canonical", **canonical)
            key_checks(db)
            db.execute("DELETE FROM job_idempotency")
            db.execute("DELETE FROM dispatches")
            db.execute("DELETE FROM jobs")
            irregular = seed_jobs(db, IRREGULAR)
            emit("ordering", side=side, contract="irregular", **irregular)
            cleanup_canonical = cleanup_checks(db, CANONICAL)
            cleanup_irregular = cleanup_checks(db, IRREGULAR)
            emit("cleanup", side=side, contract="canonical", **cleanup_canonical)
            emit("cleanup", side=side, contract="irregular", **cleanup_irregular)
            repo = PostgresHostedRepository(db)
            for _ in range(2):
                repo.billing.process_payment_event("stripe", "synthetic-replay", ACCOUNT, 20)
            assert repo.billing.account(UUID(ACCOUNT)).available_credits == 20
            emit("payment_replay", side=side, available_credits=20)
            for value, expected_key in MAILBOXES.items():
                try:
                    result = mailbox(value)[1]
                except ValueError:
                    result = None
                assert result == expected_key, (value, result, expected_key)
            emit("mailbox", side=side, cases=MAILBOXES)
            prepare_typescript(admin, url)
            return {
                "canonical": canonical,
                "irregular": irregular,
                "catalog": catalog,
                "cleanup_canonical": cleanup_canonical,
                "cleanup_irregular": cleanup_irregular,
            }
        finally:
            db.close()
    finally:
        admin.close()


def main() -> None:
    if not __debug__:
        raise SystemExit("This rehearsal requires Python assertions")
    os.chdir(ROOT)
    names = ["kenkui-neon-locale-source-20261103", "kenkui-neon-locale-target-20261103"]
    created = []
    results = {}
    urls = {}
    try:
        for name in names:
            inspected = command("docker", "container", "inspect", name, check=False)
            assert inspected.returncode != 0 and "No such container" in inspected.stderr, (
                inspected.stderr
            )
        image = json.loads(command("docker", "image", "inspect", IMAGE).stdout)[0]
        emit(
            "image",
            id=image["Id"],
            architecture=image["Architecture"],
            os=image["Os"],
            digests=image["RepoDigests"],
        )
        with tempfile.TemporaryDirectory(prefix="kenkui-locale-tls-") as folder:
            tls = Path(folder)
            command(
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=Kenkui Synthetic Locale CA",
                "-keyout",
                str(tls / "ca.key"),
                "-out",
                str(tls / "ca.crt"),
            )
            command(
                "openssl",
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(tls / "server.key"),
                "-out",
                str(tls / "server.csr"),
            )
            (tls / "extensions").write_text(
                "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
            )
            command(
                "openssl",
                "x509",
                "-req",
                "-in",
                str(tls / "server.csr"),
                "-CA",
                str(tls / "ca.crt"),
                "-CAkey",
                str(tls / "ca.key"),
                "-CAcreateserial",
                "-days",
                "1",
                "-extfile",
                str(tls / "extensions"),
                "-out",
                str(tls / "server.crt"),
            )
            # Only the leaf key/certificate are exposed to the two task containers.
            server_tls = tls / "server"
            server_tls.mkdir()
            for filename in ("server.key", "server.crt"):
                (server_tls / filename).write_bytes((tls / filename).read_bytes())
            (server_tls / "server.key").chmod(0o644)
            for side, name, port, init in (
                ("source", names[0], 55443, "--locale-provider=libc --locale=en_US.UTF8"),
                (
                    "target",
                    names[1],
                    55444,
                    "--locale-provider=builtin --builtin-locale=C.UTF-8 --locale=C.UTF-8",
                ),
            ):
                args = [
                    "docker",
                    "create",
                    "--name",
                    name,
                    "--label",
                    LABEL,
                    "--memory=512m",
                    "--cpus=1",
                    "--pids-limit=128",
                    "--shm-size=64m",
                    "--read-only",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,size=256m",
                    "--tmpfs",
                    "/var/run/postgresql:rw,size=8m",
                    "--tmpfs",
                    "/tmp:rw,size=8m",
                    "--publish",
                    f"127.0.0.1:{port}:5432",
                    "--mount",
                    f"type=bind,source={server_tls},target=/task-tls,readonly",
                    "--env",
                    "POSTGRES_PASSWORD=synthetic-locale-only",
                    "--env",
                    f"POSTGRES_INITDB_ARGS={init} --auth-host=scram-sha-256",
                    "--entrypoint",
                    "bash",
                    IMAGE,
                    "-ceu",
                    "cp /task-tls/server.* /tmp/; chown postgres:postgres /tmp/server.*; chmod 600 /tmp/server.key; exec /usr/local/bin/docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key",
                ]
                container = command(*args).stdout.strip()
                assert len(container) == 64 and all(c in "0123456789abcdef" for c in container)
                created.append(container)
                inspected = json.loads(command("docker", "container", "inspect", container).stdout)[
                    0
                ]
                host = inspected["HostConfig"]
                assert host["Memory"] == 512 * 1024 * 1024 and host["NanoCpus"] == 1000000000
                assert host["PidsLimit"] == 128 and host["ReadonlyRootfs"]
                assert host["PortBindings"] == {
                    "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
                }
                assert (
                    inspected["Config"]["Labels"].get("kenkui.synthetic-locale-rehearsal")
                    == "20261103"
                )
                emit(
                    "created",
                    side=side,
                    name=name,
                    id=container,
                    port=port,
                    limits={
                        key: host[key]
                        for key in (
                            "Memory",
                            "NanoCpus",
                            "PidsLimit",
                            "ReadonlyRootfs",
                            "Tmpfs",
                            "PortBindings",
                        )
                    },
                    mounts=inspected["Mounts"],
                )
                command("docker", "start", container)
                for _attempt in range(60):
                    ready = command(
                        "docker",
                        "exec",
                        container,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "postgres",
                        check=False,
                    )
                    if ready.returncode == 0:
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError(command("docker", "logs", container, check=False).stdout)
                emit(
                    "host_library",
                    side=side,
                    glibc=command(
                        "docker", "exec", container, "getconf", "GNU_LIBC_VERSION"
                    ).stdout.strip(),
                )
                query = urlencode(
                    {
                        "sslmode": "verify-full",
                        "sslrootcert": str(tls / "ca.crt"),
                        "connect_timeout": "5",
                    }
                )
                url = (
                    f"postgresql://postgres:synthetic-locale-only@127.0.0.1:{port}/postgres?{query}"
                )
                # Verify the server actually negotiated TLS before any application probes.
                with psycopg.connect(url) as connection:
                    assert connection.execute(
                        "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                    ).fetchone() == (True,)
                gate = PostgresDatabase(url)
                try:
                    actual = identity(gate)
                    expected = {
                        "source": ("c", "en_US.UTF8", "2.36", None),
                        "target": ("b", "C.UTF-8", "1", "C.UTF-8"),
                    }[side]
                    assert (
                        actual["provider"],
                        actual["datcollate"],
                        actual["stored"],
                        actual["datlocale"],
                    ) == expected, actual
                    assert actual["datctype"] == expected[1] and actual["actual"] == expected[2], (
                        actual
                    )
                    emit("bootstrap", side=side, **actual)
                finally:
                    gate.close()
                urls[side] = url
            for side, url in urls.items():
                results[side] = exercise(url, side)
                env = {
                    **os.environ,
                    "KENKUI_LOCALE_REHEARSAL_URL": url,
                    "KENKUI_LOCALE_REHEARSAL_CA": str(tls / "ca.crt"),
                }
                tests = subprocess.run(
                    ["node_modules/.bin/vitest", "run", "test/paired-locale.rehearsal.test.ts"],
                    cwd=ROOT / "hosted-api",
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=120,
                )
                emit(
                    "typescript",
                    side=side,
                    returncode=tests.returncode,
                    stdout=tests.stdout,
                    stderr=tests.stderr,
                )
                assert tests.returncode == 0
            assert results["source"]["canonical"] == results["target"]["canonical"]
            assert results["source"]["catalog"] == results["target"]["catalog"]
            assert results["source"]["cleanup_canonical"] == results["target"]["cleanup_canonical"]
            emit(
                "comparison",
                canonical_parity=True,
                catalogs_parity=True,
                canonical_cleanup_parity=True,
                irregular_order_differs=results["source"]["irregular"]
                != results["target"]["irregular"],
                irregular_cleanup_differs=results["source"]["cleanup_irregular"]
                != results["target"]["cleanup_irregular"],
            )
    finally:
        failures = []
        for container in reversed(created):
            # IDs, never a prefix, label selection or a name that could have been reused.
            removal = command("docker", "rm", "--force", "--volumes", container, check=False)
            emit(
                "cleanup_container",
                id=container,
                returncode=removal.returncode,
                stdout=removal.stdout,
                stderr=removal.stderr,
            )
            if removal.returncode:
                failures.append(container)
        assert not failures, failures


if __name__ == "__main__":
    main()
