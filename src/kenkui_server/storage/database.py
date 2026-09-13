"""SQLite connection lifecycle and ordered local schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

from kenkui_server.storage.migrations import MIGRATIONS


class QueryResult:
    """Rows copied while holding the connection lock; no shared live cursor."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows, self.rows = self.rows, []
        return rows


class Database:
    """One local SQLite/WAL database and its authoritative schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    def migrate(self) -> None:
        """Apply each known schema migration exactly once."""
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, upgrade in MIGRATIONS:
                if version not in applied:
                    upgrade(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
                    )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit an atomic local persistence change or roll it back."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def query(self, statement: str, parameters: tuple[Any, ...] = ()) -> QueryResult:
        """Serialize reads with writes on this shared SQLite connection."""
        with self._lock:
            return QueryResult(self.connection.execute(statement, parameters).fetchall())

    def journal_mode(self) -> str:
        """Return the active SQLite journal mode."""
        return str(self.connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def close(self) -> None:
        """Close the local database connection."""
        self.connection.close()
