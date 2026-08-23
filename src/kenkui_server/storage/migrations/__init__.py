"""Versioned SQLite schema migrations."""

from kenkui_server.storage.migrations.m0001_initial import VERSION as INITIAL_VERSION
from kenkui_server.storage.migrations.m0001_initial import upgrade as initial_upgrade
from kenkui_server.storage.migrations.m0002_dispatch_idempotency import (
    VERSION as IDEMPOTENCY_VERSION,
)
from kenkui_server.storage.migrations.m0002_dispatch_idempotency import (
    upgrade as idempotency_upgrade,
)

MIGRATIONS = (
    (INITIAL_VERSION, initial_upgrade),
    (IDEMPOTENCY_VERSION, idempotency_upgrade),
)
