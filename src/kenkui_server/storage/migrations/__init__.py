"""Versioned SQLite schema migrations."""

from kenkui_server.storage.migrations.m0001_initial import VERSION, upgrade

MIGRATIONS = ((VERSION, upgrade),)
