"""Structured logging utilities for HTTP request observability."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Keep library event context as well as application events and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        # HTTP events already use JSON messages; keep their existing fields.
        if record.name.startswith("kenkui_server"):
            try:
                message = json.loads(record.getMessage())
            except ValueError:
                message = None
            if isinstance(message, dict):
                payload.update(message)
        for name, value in record.__dict__.items():
            if name not in _RECORD_FIELDS and isinstance(value, (str, int, float, bool)):
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_logging() -> None:
    """Emit Kenkui INFO events even when a host already configured root logging.

    Configure only our namespaces: enabling provider SDK INFO/DEBUG output can
    expose request content. Repeated worker calls must not add duplicate handlers.
    """
    for name in ("kenkui", "kenkui_server"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.disabled = False
        logger.propagate = False
        if not any(handler.get_name() == "kenkui-json" for handler in logger.handlers):
            handler = logging.StreamHandler(sys.stdout)
            handler.set_name("kenkui-json")
            handler.setLevel(logging.INFO)
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)


def log_event(
    logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any
) -> None:
    """Emit a stable structured event through a module logger."""
    logger.log(
        level,
        json.dumps({"event": event, **fields}, sort_keys=True, separators=(",", ":")),
    )
