"""Bounded subprocess supervision backed by durable execution leases."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Lock, Thread

from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories

LOGGER = logging.getLogger(__name__)


class LocalProcessRunner:
    """Bounded dispatch with optional desktop-owned worker lifetimes."""

    def __init__(
        self,
        database_path: str | Path,
        assets_root: str | Path,
        *,
        fixture_mode: bool = False,
        max_jobs: int = 2,
        render_workers: int = 1,
        stop_workers_on_close: bool = False,
    ) -> None:
        if max_jobs < 1 or render_workers < 1:
            raise ValueError("worker limits must be positive")
        self._database_path = str(database_path)
        self._assets_root = str(assets_root)
        self._fixture_mode = fixture_mode
        self._max_jobs = max_jobs
        self._render_workers = render_workers
        self._stop_workers_on_close = stop_workers_on_close
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._shutdown_error: Exception | None = None

    def start(self, dispatch_id: str) -> None:
        """Schedule committed work; scanning also recovers failed process starts."""
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("Local process runner is closed")
            if self._thread is None:
                self._thread = Thread(target=self._supervise, daemon=True)
                self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10 if self._stop_workers_on_close else 5)
            if self._stop_workers_on_close and self._thread.is_alive():
                raise RuntimeError("Local workers did not stop before the shutdown deadline")
            if self._shutdown_error is not None:
                raise RuntimeError("Local worker shutdown failed") from self._shutdown_error

    def _supervise(self) -> None:
        database = Database(self._database_path)
        repositories = Repositories(database)
        children: list[tuple[subprocess.Popen[bytes], str, str]] = []
        try:
            while not self._stop.is_set():
                for child, dispatch_id, finished_token in children[:]:
                    if child.poll() is not None:
                        if child.stdin is not None:
                            child.stdin.close()
                        repositories.release_execution(dispatch_id, finished_token)
                        children.remove((child, dispatch_id, finished_token))
                for dispatch in repositories.dispatches.list_incomplete():
                    if self._stop.is_set():
                        break
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
                    if self._stop_workers_on_close:
                        command = [
                            sys.executable,
                            "-m",
                            "kenkui_server.compute.managed_worker",
                            *command,
                        ]
                    try:
                        child = subprocess.Popen(
                            command,
                            start_new_session=True,
                            stdin=subprocess.PIPE if self._stop_workers_on_close else None,
                        )
                    except OSError:
                        LOGGER.exception("worker_start_failed", extra={"dispatch_id": dispatch.id})
                        repositories.release_execution(dispatch.id, token)
                    else:
                        children.append((child, dispatch.id, token))
                self._stop.wait(0.1)
        except Exception:
            LOGGER.exception("local_dispatcher_failed")
        finally:
            try:
                if self._stop_workers_on_close:
                    self._stop_children(repositories, children)
                else:
                    # Standalone API workers retain their existing lifetime.
                    for child, _, _ in children:
                        Thread(target=child.wait, daemon=True).start()
            except Exception as error:
                self._shutdown_error = error
                LOGGER.exception("local_worker_shutdown_failed")
            finally:
                database.close()

    def _stop_children(
        self,
        repositories: Repositories,
        children: list[tuple[subprocess.Popen[bytes], str, str]],
    ) -> None:
        try:
            for _, dispatch_id, _ in children:
                dispatch = repositories.dispatches.get(dispatch_id)
                repositories.request_cancellation(dispatch.job_id)
            # Give cancellation tokens time to stop synthesis and remove partial output.
            deadline = time.monotonic() + 3
            while any(child.poll() is None for child, _, _ in children):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        finally:
            # Close every control pipe before waiting; shutdown time does not
            # scale with the number of workers. EOF also works if the API dies.
            for child, _, _ in children:
                if child.stdin is not None:
                    child.stdin.close()
            deadline = time.monotonic() + 5
            for child, dispatch_id, token in children:
                child.wait(timeout=max(0, deadline - time.monotonic()))
                repositories.release_execution(dispatch_id, token)
