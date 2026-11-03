"""Full synthetic PostgreSQL -> age -> independent restore (no provider data)."""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from kenkui_server.maintenance import write_logical_backup
from kenkui_server.storage.postgres_database import PostgresDatabase

URL = "postgresql://postgres@127.0.0.1:55438/postgres"
pytestmark = pytest.mark.skipif(
    os.environ.get("KENKUI_TEST_DATABASE_URL") != URL
    or not all(shutil.which(tool) for tool in ("age", "age-keygen", "pg_dump", "pg_restore")),
    reason="requires disposable local PostgreSQL and official age/PostgreSQL clients",
)


def snapshot(connection):
    tables = connection.execute(
        "SELECT schemaname,tablename FROM pg_tables "
        "WHERE schemaname IN ('public','neon_auth') ORDER BY schemaname,tablename"
    ).fetchall()
    return {
        (schema, table): connection.execute(
            sql.SQL(
                'SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text COLLATE "C"'
            ).format(sql.Identifier(schema), sql.Identifier(table))
        ).fetchall()
        for schema, table in tables
    }


def test_encrypted_full_database_restores_auth_and_private_release_evidence(tmp_path, monkeypatch):
    assert os.environ["KENKUI_TEST_DATABASE_URL"] == URL
    names = ["backup_test_" + uuid4().hex for _ in range(2)]
    actual_run = subprocess.run
    identity = tmp_path / "identity.txt"
    # Synthetic private key remains only in this temporary directory and captured subprocess output.
    actual_run(["age-keygen", "-o", str(identity)], capture_output=True, check=True)
    identity.chmod(0o600)
    recipient = actual_run(
        ["age-keygen", "-y", str(identity)], capture_output=True, text=True, check=True
    ).stdout.strip()
    created = []
    with psycopg.connect(URL, autocommit=True) as admin:
        try:
            for name in names:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created.append(name)
            source_url, target_url = [URL.removesuffix("postgres") + name for name in names]
            db = PostgresDatabase(source_url)
            try:
                db.migrate()
                with db.connection() as conn:
                    conn.execute("CREATE SCHEMA neon_auth")
                    conn.execute("CREATE TABLE neon_auth.credentials (id text, signing_key text)")
                    conn.execute(
                        "INSERT INTO neon_auth.credentials "
                        "VALUES ('fixture','private-auth-evidence')"
                    )
                    conn.execute("""INSERT INTO migration_directory_runs
                        (id,environment,issuer,captured_at,watermark,digest,mode,frozen,
                         invited_emails,restored_owners,source_only,matched_restored_owners)
                        VALUES (gen_random_uuid(),'staging','fixture',now()-interval '3 days',
                                'private-release-watermark',repeat('a',64),'final',true,'{}',0,0,0)""")
                    conn.execute(
                        "INSERT INTO migration_directory_active "
                        "SELECT environment,id FROM migration_directory_runs"
                    )
                with psycopg.connect(source_url) as source:
                    before = snapshot(source)

                def run(command, **kwargs):
                    if command[0] == "pg_dump":
                        assert "--schema" not in command and "--exclude-schema" not in command
                        assert kwargs["env"]["PGSSLMODE"] == "verify-full"
                        # This loopback-only trust server has no TLS; keep production TLS unchanged.
                        kwargs["env"] = dict(kwargs["env"], PGSSLMODE="disable")
                    return actual_run(command, **kwargs)

                encrypted = tmp_path / "download.dump.age"

                class Client:
                    def upload_file(self, path, bucket, key):
                        assert key.endswith("2026-11-02.dump.age")
                        assert not Path(path).with_suffix("").exists()
                        data = Path(path).read_bytes()
                        assert data.startswith(b"age-encryption.org/v1\n")
                        assert b"private-auth-evidence" not in data
                        encrypted.write_bytes(data)

                    def get_paginator(self, operation):
                        return self

                    def paginate(self, **kwargs):
                        yield {}

                monkeypatch.setattr("kenkui_server.maintenance.subprocess.run", run)
                write_logical_backup(
                    Client(),
                    bucket="synthetic",
                    environment="staging",
                    database_url=source_url,
                    age_recipient=recipient,
                    now=datetime(2026, 11, 2, tzinfo=UTC),
                )
            finally:
                db.close()
            archive = tmp_path / "restore.dump"
            actual_run(
                [
                    "age",
                    "--decrypt",
                    "--identity",
                    str(identity),
                    "--output",
                    str(archive),
                    str(encrypted),
                ],
                capture_output=True,
                check=True,
            )
            actual_run(
                [
                    "pg_restore",
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                    "--dbname",
                    target_url,
                    str(archive),
                ],
                capture_output=True,
                check=True,
            )
            with psycopg.connect(target_url) as target:
                assert snapshot(target) == before
                assert target.execute(
                    "SELECT signing_key FROM neon_auth.credentials"
                ).fetchone() == ("private-auth-evidence",)
        finally:
            for name in reversed(created):
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
