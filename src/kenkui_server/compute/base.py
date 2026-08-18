"""Minimal process-runner boundary for durable local dispatch."""

from __future__ import annotations

from typing import Protocol


class ProcessRunner(Protocol):
    """Start execution only after durable admission has committed."""

    def start(self, dispatch_id: str) -> None:
        """Schedule a durable dispatch attempt without changing its truth."""
        ...
