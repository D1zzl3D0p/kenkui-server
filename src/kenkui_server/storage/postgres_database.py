"""Pooled psycopg connections with thread-local transaction ownership."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import local
from typing import Any


class BufferedCursor:
    def __init__(self, cursor: Any) -> None:
        self.rowcount = cursor.rowcount
        self.rows: list[Mapping[str, Any]] = cursor.fetchall() if cursor.description else []

    def fetchone(self) -> Mapping[str, Any] | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[Mapping[str, Any]]:
        rows, self.rows = self.rows, []
        return rows


class PostgresDatabase:
    def __init__(
        self,
        url: str,
        *,
        schema: str | None = None,
        connect_timeout: int | None = None,
        idle: bool = False,
    ) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        kwargs: dict[str, Any] = {"autocommit": True, "row_factory": dict_row}
        if connect_timeout is not None:
            kwargs["connect_timeout"] = connect_timeout
        if schema is not None:
            if not schema.replace("_", "").isalnum():
                raise ValueError("invalid database schema")
            kwargs["options"] = f"-c search_path={schema}"
        pool_options: dict[str, Any] = {}
        if idle:
            pool_options.update(max_idle=60, check=ConnectionPool.check_connection)
        self._pool = ConnectionPool(
            url,
            kwargs=kwargs,
            min_size=0 if idle else 1,
            max_size=8,
            timeout=10,
            open=True,
            **pool_options,
        )
        self._idle = idle
        self._state = local()

    def wait_until_ready(self, *, timeout: float) -> None:
        """Wait for initial connectivity without replaying SQL or transactions."""
        from psycopg_pool import PoolTimeout

        logger = logging.getLogger(__name__)
        started = time.monotonic()
        try:
            if self._idle:
                # wait() is already satisfied at min_size=0. Checkout also runs
                # the validation callback before returning a usable connection.
                with self._pool.connection(timeout=timeout):
                    pass
            else:
                self._pool.wait(timeout=timeout)
        except PoolTimeout:
            self._pool.close()
            logger.exception("postgres_startup_timeout budget_seconds=%s", timeout)
            raise
        elapsed = time.monotonic() - started
        if elapsed >= 10:
            logger.warning("postgres_startup_recovered elapsed_seconds=%.2f", elapsed)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        current = getattr(self._state, "connection", None)
        if current is not None:
            yield current
        else:
            with self._pool.connection() as connection:
                yield connection

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> BufferedCursor:
        with self.connection() as connection:
            return BufferedCursor(connection.execute(statement, parameters or None))

    @contextmanager
    def transaction(self) -> Iterator[PostgresDatabase]:
        with self.connection() as connection:
            previous = getattr(self._state, "connection", None)
            self._state.connection = connection
            try:
                with connection.transaction():
                    yield self
            finally:
                self._state.connection = previous

    def migrate(self, directory: Path | None = None) -> None:
        root = directory or Path(__file__).resolve().parents[1] / "migrations/postgresql"
        if directory is None and not root.exists():
            root = Path(__file__).resolve().parents[3] / "migrations/postgresql"
        if not list(root.glob("*.sql")):
            raise ValueError("PostgreSQL migration files are missing")
        with self.transaction():
            self.execute("SELECT pg_advisory_xact_lock(1262833235)")
            self.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
            applied = {
                row["name"] for row in self.execute("SELECT name FROM schema_migrations").fetchall()
            }
            for path in sorted(root.glob("*.sql")):
                if path.name not in applied:
                    # Migration files contain plain DDL, without procedural blocks.
                    for statement in path.read_text().split(";"):
                        if statement.strip():
                            self.execute(statement)
                    self.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))

    def close(self) -> None:
        self._pool.close()
