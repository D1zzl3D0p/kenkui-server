"""Worker diagnostics survive host logging configuration without duplicate output."""

import json
import logging

import pytest
from kenkui.observability import log_event as library_event

from kenkui_server.observability import configure_logging, log_event


@pytest.fixture
def configured_logging(capsys):
    loggers = [logging.getLogger(name) for name in ("kenkui", "kenkui_server")]
    previous = [
        (logger.level, logger.propagate, logger.disabled, logger.handlers[:]) for logger in loggers
    ]
    try:
        yield
    finally:
        for logger, (level, propagate, disabled, handlers) in zip(loggers, previous, strict=True):
            for handler in logger.handlers:
                if handler not in handlers:
                    handler.close()
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


def test_library_info_context_emitted_once_with_preconfigured_root(configured_logging, capsys):
    # pytest already installs root handlers: basicConfig() alone is a no-op here.
    configure_logging()
    configure_logging()
    library_event(
        logging.getLogger("kenkui._characters.llm"),
        "model_call_failed",
        context={"attempt": 3, "error": "Timeout", "status_code": 408},
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 1
    assert events[0]["level"] == "INFO"
    assert events[0]["event"] == "model_call_failed"
    assert events[0]["attempt"] == 3
    assert events[0]["error"] == "Timeout"
    assert events[0]["status_code"] == 408


def test_http_fields_and_worker_tracebacks_are_preserved(configured_logging, capsys):
    configure_logging()
    logger = logging.getLogger("kenkui_server.worker")
    log_event(logger, "request.completed", requestId="abc", statusCode=200)
    try:
        raise RuntimeError("checkpoint unavailable")
    except RuntimeError:
        logger.exception("job_failed", extra={"job_id": "job-123"})
    first, second = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert first["event"] == "request.completed"
    assert first["requestId"] == "abc"
    assert second["job_id"] == "job-123"
    assert "RuntimeError: checkpoint unavailable" in second["exception"]
