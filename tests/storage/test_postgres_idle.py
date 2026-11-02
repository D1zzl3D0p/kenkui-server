"""Exercise idle checkout/retry behavior with a real pool and no wire dependency."""

from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg_pool import PoolClosed, PoolTimeout
from psycopg_pool.base import AttemptWithBackoff

from kenkui_server.storage.postgres_database import PostgresDatabase


class WireConnection:
    """Only the wire boundary is fake: pool scheduling and validation stay real."""

    def __init__(self, **kwargs):
        self.autocommit = kwargs["autocommit"]
        self.options = kwargs
        self.pgconn = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.closed = False
        self.statements = []
        self.probes = 0
        self.transactions = 0
        self.commit_error = None

    def execute(self, statement, parameters=None):
        if self.closed:
            raise psycopg.OperationalError("connection is closed")
        if statement == "":
            self.probes += 1
        else:
            self.statements.append((statement, parameters))
        return SimpleNamespace(rowcount=1, description=True, fetchall=lambda: [{"value": 1}])

    @contextmanager
    def transaction(self):
        self.transactions += 1
        self.pgconn.transaction_status = TransactionStatus.INTRANS
        try:
            yield
            if self.commit_error:
                # The server may have committed before the transport failed.
                raise self.commit_error
        finally:
            self.pgconn.transaction_status = TransactionStatus.IDLE

    def close(self):
        self.closed = True
        self.pgconn.transaction_status = TransactionStatus.UNKNOWN

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def wire_connections(monkeypatch):
    connections = []

    def connect(conninfo, **kwargs):
        connection = WireConnection(**kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg.Connection, "connect", connect)
    monkeypatch.setattr(AttemptWithBackoff, "INITIAL_DELAY", 0.01)
    return connections


def test_idle_constructor_does_not_connect(wire_connections):
    database = PostgresDatabase("", idle=True)
    try:
        assert database._pool.get_stats()["pool_size"] == 0
        assert wire_connections == []
        assert database._pool.min_size == 0
        assert database._pool.max_size == 8
        assert database._pool.max_idle == 60
        assert database._pool.timeout == 10
    finally:
        database.close()


def test_idle_wait_establishes_one_connection(wire_connections):
    database = PostgresDatabase("", idle=True, connect_timeout=10, schema="test_schema")
    try:
        database.wait_until_ready(timeout=2)
        assert len(wire_connections) == 1
        connection = wire_connections[0]
        assert connection.probes == 1
        assert connection.options == {
            "autocommit": True,
            "row_factory": dict_row,
            "connect_timeout": 10,
            "options": "-c search_path=test_schema",
        }
        assert database._pool.get_stats()["pool_available"] == 1
        assert database.execute("SELECT 1 AS value").fetchone() == {"value": 1}
        assert len(wire_connections) == 1
    finally:
        database.close()


def test_stale_idle_connection_is_replaced_before_transaction(wire_connections):
    database = PostgresDatabase("", idle=True)
    try:
        database.wait_until_ready(timeout=2)
        stale = wire_connections[0]
        stale.close()

        with database.transaction():
            assert database.execute("SELECT 1 AS value").fetchone() == {"value": 1}
            # Transaction-local calls must keep the validated checkout.
            assert database.execute("SELECT 1 AS value").fetchone() == {"value": 1}

        # Replacement and growth can race in the real pool's background workers;
        # only one validated connection may own the caller's transaction.
        (replacement,) = [connection for connection in wire_connections if connection.transactions]
        assert stale.statements == []
        assert stale.transactions == 0
        assert replacement.probes == 1
        assert replacement.transactions == 1
        assert replacement.statements == [("SELECT 1 AS value", None), ("SELECT 1 AS value", None)]
    finally:
        database.close()


def test_transaction_error_is_not_replayed(wire_connections):
    database = PostgresDatabase("", idle=True)
    try:
        database.wait_until_ready(timeout=2)
        connection = wire_connections[0]
        ambiguous = psycopg.OperationalError("connection lost while committing")
        connection.commit_error = ambiguous
        bodies = 0

        with pytest.raises(psycopg.OperationalError) as error, database.transaction():
            bodies += 1
            database.execute("INSERT INTO ledger VALUES (1)")

        assert error.value is ambiguous
        assert bodies == 1
        assert connection.transactions == 1
        assert connection.statements == [("INSERT INTO ledger VALUES (1)", None)]
        # Transaction-local ownership must also be cleared on failure.
        assert database.execute("SELECT 1 AS value").fetchone() == {"value": 1}
        assert connection.probes == 3
    finally:
        database.close()


def test_idle_readiness_outage_uses_budget_and_closes_pool(monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("database unavailable")

    monkeypatch.setattr(psycopg.Connection, "connect", unavailable)
    monkeypatch.setattr(AttemptWithBackoff, "INITIAL_DELAY", 0.01)
    database = PostgresDatabase("", idle=True)
    try:
        with pytest.raises(PoolTimeout):
            database.wait_until_ready(timeout=0.1)
        assert "postgres_startup_timeout" in caplog.text
        with pytest.raises(PoolClosed), database.connection():
            pytest.fail("timed out pool should be closed")
    finally:
        database.close()


@pytest.mark.parametrize("idle", [False, True])
def test_schema_validation_precedes_pool_creation(wire_connections, idle):
    with pytest.raises(ValueError, match="invalid database schema"):
        PostgresDatabase("", schema="invalid;schema", idle=idle)
    assert wire_connections == []


@pytest.mark.parametrize("setting", [None, "0", "true", "1"])
@pytest.mark.parametrize("entrypoint", ["api", "worker", "migrate", "recover"])
def test_hosted_idle_requires_explicit_environment_setting(
    monkeypatch, tmp_path, wire_connections, setting, entrypoint
):
    import sys

    import workos

    from kenkui_server import hosted
    from kenkui_server.compute.hosted import HostedJobRunner
    from kenkui_server.jobs.dispatcher import HostedDispatcher

    if setting is None:
        monkeypatch.delenv("KENKUI_DATABASE_IDLE", raising=False)
    else:
        monkeypatch.setenv("KENKUI_DATABASE_IDLE", setting)
    monkeypatch.setenv("DATABASE_URL", "")
    databases = []

    def database_factory(url, **kwargs):
        database = PostgresDatabase(url, **kwargs)
        databases.append(database)
        return database

    monkeypatch.setattr(
        hosted,
        "required",
        lambda name: {
            "WORKOS_REDIRECT_URI": "https://api.example/callback",
            "KENKUI_WEB_ORIGIN": "https://web.example",
            "KENKUI_SESSION_SECRET": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=",
        }.get(name, "test-value"),
    )
    monkeypatch.setattr(hosted, "PostgresDatabase", database_factory)
    monkeypatch.setattr(hosted, "object_store", object)
    monkeypatch.setattr(hosted.kk, "list_voices", lambda: ())
    monkeypatch.setattr(hosted, "select_hosted_voices", lambda *args: ())
    monkeypatch.setattr(workos, "WorkOSClient", lambda **kwargs: object())
    monkeypatch.setattr(PostgresDatabase, "migrate", lambda self: None)
    monkeypatch.setattr(HostedDispatcher, "recover", lambda self: None)
    try:
        if entrypoint == "api":
            hosted.create_hosted_app()
        elif entrypoint == "worker":
            databases.append(HostedJobRunner("", object(), tmp_path, lease=("d", "t"))._database())
        else:
            monkeypatch.setattr(sys, "argv", ["kenkui-hosted", entrypoint])
            hosted.main()
        assert len(databases) == 1
        assert databases[0]._pool.min_size == (0 if setting == "1" else 1)
    finally:
        for database in databases:
            database.close()
