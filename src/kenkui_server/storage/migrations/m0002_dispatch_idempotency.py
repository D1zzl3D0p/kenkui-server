"""Durable idempotency records for atomic local admission."""

from __future__ import annotations

import sqlite3

VERSION = 2


def upgrade(connection: sqlite3.Connection) -> None:
    """Persist one canonical job ID for each client retry key."""
    connection.execute(
        """
        CREATE TABLE job_idempotency (
            key TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id)
        )
        """
    )
