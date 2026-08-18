"""Local multiprocessing implementation of durable dispatch execution."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path

from kenkui_server.worker import run_dispatch


class LocalProcessRunner:
    """Spawn independent workers that re-open authoritative local state."""

    def __init__(self, database_path: str | Path, assets_root: str | Path, *, fixture_mode: bool = False) -> None:
        self._database_path = str(database_path)
        self._assets_root = str(assets_root)
        self._fixture_mode = fixture_mode

    def start(self, dispatch_id: str) -> None:
        """Start a process after the caller has committed admission."""
        process = get_context("spawn").Process(
            target=run_dispatch,
            args=(self._database_path, self._assets_root, dispatch_id, self._fixture_mode),
            daemon=True,
        )
        process.start()
