"""Exercise real pool background retries without a network dependency."""

from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg_pool import PoolClosed, PoolTimeout
from psycopg_pool.base import AttemptWithBackoff

from kenkui_server.storage.postgres_database import PostgresDatabase


def test_startup_wait_survives_transient_connection_failure(monkeypatch, caplog):
    connection = MagicMock()
    connect = MagicMock(side_effect=[psycopg.OperationalError("temporary outage"), connection])
    monkeypatch.setattr(psycopg.Connection, "connect", connect)
    monkeypatch.setattr(AttemptWithBackoff, "INITIAL_DELAY", 0.01)

    database = PostgresDatabase("", connect_timeout=10)
    try:
        database.wait_until_ready(timeout=2)
        assert connect.call_count == 2
        assert connect.call_args.kwargs["connect_timeout"] == 10
        assert database._pool.get_stats()["pool_available"] == 1
        assert "temporary outage" in caplog.text
        connection.execute.assert_not_called()
    finally:
        database.close()
    connection.close.assert_called_once()


def test_persistent_outage_exhausts_budget_and_closes_pool(monkeypatch, caplog):
    connect = MagicMock(side_effect=psycopg.OperationalError("database unavailable"))
    monkeypatch.setattr(psycopg.Connection, "connect", connect)
    monkeypatch.setattr(AttemptWithBackoff, "INITIAL_DELAY", 0.01)

    database = PostgresDatabase("", connect_timeout=10)
    try:
        with pytest.raises(PoolTimeout):
            database.wait_until_ready(timeout=0.1)
        assert connect.call_count >= 2
        assert "database unavailable" in caplog.text
        assert "postgres_startup_timeout" in caplog.text
        with pytest.raises(PoolClosed), database.connection():
            pytest.fail("timed out pool should be closed")
    finally:
        database.close()


def test_default_callers_keep_existing_connection_options(monkeypatch):
    connect = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(psycopg.Connection, "connect", connect)
    database = PostgresDatabase("")
    try:
        database.wait_until_ready(timeout=2)
        assert "connect_timeout" not in connect.call_args.kwargs
    finally:
        database.close()
