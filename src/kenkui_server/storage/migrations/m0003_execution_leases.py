"""Expiring execution ownership and durable worker failure details."""

import sqlite3

VERSION = 3


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE execution_leases (
        dispatch_id TEXT PRIMARY KEY REFERENCES dispatches(id),
        token TEXT NOT NULL,
        expires_at REAL NOT NULL,
        attempts INTEGER NOT NULL CHECK (attempts > 0)
    )""")
    connection.execute("""CREATE TABLE job_failures (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id),
        code TEXT NOT NULL,
        message TEXT NOT NULL
    )""")
