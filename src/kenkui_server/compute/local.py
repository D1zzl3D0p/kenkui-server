"""Bounded subprocess supervision backed by durable execution leases."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from threading import Event, Lock, Thread

from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories

LOGGER = logging.getLogger(__name__)


class LocalProcessRunner:
    """Wake a bounded dispatcher; subprocesses survive API restarts safely."""

    def __init__(
        self,
        database_path: str | Path,
        assets_root: str | Path,
        *,
        fixture_mode: bool = False,
        max_jobs: int = 2,
        render_workers: int = 1,
    ) -> None:
        if max_jobs < 1 or render_workers < 1:
            raise ValueError("worker limits must be positive")
        self._database_path = str(database_path)
        self._assets_root = str(assets_root)
        self._fixture_mode = fixture_mode
        self._max_jobs = max_jobs
        self._render_workers = render_workers
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self, dispatch_id: str) -> None:
        """Schedule committed work; scanning also recovers failed process starts."""
        with self._lock:
            if self._thread is None:
                self._thread = Thread(target=self._supervise, daemon=True)
                self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _supervise(self) -> None:
        database = Database(self._database_path)
        repositories = Repositories(database)
        children: list[tuple[subprocess.Popen[bytes], str, str]] = []
        try:
            while not self._stop.is_set():
                for child, dispatch_id, finished_token in children[:]:
                    if child.poll() is not None:
                        repositories.release_execution(dispatch_id, finished_token)
                        children.remove((child, dispatch_id, finished_token))
                for dispatch in repositories.dispatches.list_incomplete():
                    token = repositories.claim_execution(dispatch.id, limit=self._max_jobs)
                    if token is None:
                        continue
                    command = [
                        sys.executable,
                        "-m",
                        "kenkui_server.worker",
                        self._database_path,
                        self._assets_root,
                        dispatch.id,
                        token,
                        str(self._render_workers),
                    ]
                    if self._fixture_mode:
                        command.append("--fixture")
                    try:
                        child = subprocess.Popen(command, start_new_session=True)
                    except OSError:
                        LOGGER.exception("worker_start_failed", extra={"dispatch_id": dispatch.id})
                        repositories.release_execution(dispatch.id, token)
                    else:
                        children.append((child, dispatch.id, token))
                self._stop.wait(0.1)
        except Exception:
            LOGGER.exception("local_dispatcher_failed")
        finally:
            database.close()
            # Workers own their leases and can finish after API shutdown.
            # Reap them without making server shutdown wait for synthesis.
            for child, _, _ in children:
                Thread(target=child.wait, daemon=True).start()
