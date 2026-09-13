"""Pooled psycopg connections with thread-local transaction ownership."""

from __future__ import annotations

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
    def __init__(self, url: str, *, schema: str | None = None) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        kwargs: dict[str, Any] = {"autocommit": True, "row_factory": dict_row}
        if schema is not None:
            if not schema.replace("_", "").isalnum():
                raise ValueError("invalid database schema")
            kwargs["options"] = f"-c search_path={schema}"
        self._pool = ConnectionPool(
            url, kwargs=kwargs, min_size=1, max_size=8, timeout=10, open=True
        )
        self._state = local()

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
