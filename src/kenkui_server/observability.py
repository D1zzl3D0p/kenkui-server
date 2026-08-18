"""Structured logging utilities for HTTP request observability."""

import json
import logging
import sys
from typing import Any


def configure_logging() -> None:
    """Configure process logging to emit one JSON object per record."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


def log_event(
    logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any
) -> None:
    """Emit a stable structured event through a module logger."""
    logger.log(
        level,
        json.dumps({"event": event, **fields}, sort_keys=True, separators=(",", ":")),
    )
