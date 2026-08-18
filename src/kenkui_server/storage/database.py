"""SQLite connection lifecycle and ordered local schema migrations."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

from kenkui_server.storage.migrations import MIGRATIONS


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
                    connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))

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

    def journal_mode(self) -> str:
        """Return the active SQLite journal mode."""
        return str(self.connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def close(self) -> None:
        """Close the local database connection."""
        self.connection.close()
