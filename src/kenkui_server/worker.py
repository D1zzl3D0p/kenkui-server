"""One local worker attempt reconstructed entirely from durable state."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

import kenkui as kk

from kenkui_server.jobs.models import Artifact, Job, JobStatus
from kenkui_server.jobs.pipeline import pipeline_from_job
from kenkui_server.jobs.transitions import (
    Cancelled,
    Completed,
    DispatchRequested,
    Failed,
    ProgressReported,
    transition,
)
from kenkui_server.storage.assets import AssetStore
from kenkui_server.storage.database import Database
from kenkui_server.storage.repositories import Repositories, StaleWriteError


class LocalJobRunner:
    """Execute one claimed local dispatch using public Kenkui operations only."""

    def __init__(self, database_path: str | Path, assets_root: str | Path, *, fixture_mode: bool = False) -> None:
        self._database_path = Path(database_path)
        self._assets_root = Path(assets_root)
        self._fixture_mode = fixture_mode

    def run(self, dispatch_id: str) -> None:
        database = Database(self._database_path)
        try:
            self._run(Repositories(database), AssetStore(self._assets_root), dispatch_id)
        finally:
            database.close()

    def _update(self, repositories: Repositories, job: Job, event_type: str) -> bool:
        """Apply a transition and append its event in one guarded transaction."""
        try:
            repositories.update_job_and_append_event(
                job, expected_version=job.version - 1, event_type=event_type
            )
        except StaleWriteError:
            return False
        return True

    def _run(self, repositories: Repositories, store: AssetStore, dispatch_id: str) -> None:
        dispatch = repositories.dispatches.get(dispatch_id)
        if dispatch.status != "pending":
            return
        job = repositories.jobs.get(dispatch.job_id)
        if job.status in {JobStatus.CANCELLED, JobStatus.CANCEL_REQUESTED}:
            if job.status is JobStatus.CANCEL_REQUESTED:
                self._cancel_if_requested(repositories, job.id)
            repositories.dispatches.update(
                replace(dispatch, status="cancelled", version=dispatch.version + 1),
                expected_version=dispatch.version,
            )
            return
        if job.status is JobStatus.QUEUED:
            running = transition(job, DispatchRequested())
            if not self._update(repositories, running, "running"):
                return
        elif job.status is JobStatus.RUNNING:
            running = job
        else:
            return
        try:
            repositories.dispatches.update(
                replace(dispatch, status="running", version=dispatch.version + 1),
                expected_version=dispatch.version,
            )
        except StaleWriteError:
            return
        output: Path | None = None

        try:
            asset = repositories.assets.get(running.spec.source_id)
            output = store.artifact_path(running.id)
            if self._fixture_mode:
                output.write_bytes(b"KENKUI-FIXTURE-M4B\n")
            else:
                cancellation = kk.CancellationToken()
                stop_polling = Event()

                def poll_cancellation() -> None:
                    polling_database = Database(self._database_path)
                    try:
                        polling_repositories = Repositories(polling_database)
                        while not stop_polling.wait(0.05):
                            if polling_repositories.jobs.get(running.id).status is JobStatus.CANCEL_REQUESTED:
                                cancellation.cancel()
                                return
                    finally:
                        polling_database.close()

                poller = Thread(target=poll_cancellation, daemon=True)
                poller.start()

                def on_event(event: kk.ExecutionEvent) -> None:
                    current = repositories.jobs.get(running.id)
                    if current.status is JobStatus.CANCEL_REQUESTED:
                        cancellation.cancel()
                        return
                    # Not every event reports progress. CastResolved carries a
                    # stage but no counts, and forwarding it as progress would
                    # publish a duplicate frame at the current numbers.
                    if not hasattr(event, "completed"):
                        return
                    completed = event.completed
                    total = getattr(event, "total", current.progress.total)
                    stage = getattr(event, "stage", current.progress.stage)
                    if completed < current.progress.completed or total < completed:
                        return
                    next_job = transition(current, ProgressReported(stage, completed, total))
                    self._update(repositories, next_job, "progress")

                try:
                    pipeline_from_job(running.spec, asset.path).write(
                        output, on_event=on_event, cancel=cancellation
                    )
                finally:
                    stop_polling.set()
                    poller.join()
            current = repositories.jobs.get(running.id)
            if current.status is JobStatus.CANCEL_REQUESTED:
                output.unlink(missing_ok=True)
                self._cancel_if_requested(repositories, running.id)
                return
            completed = transition(current, Completed())
            if self._update(repositories, completed, "completed"):
                repositories.artifacts.put(Artifact(str(uuid4()), completed.id, str(output), "m4b"))
            else:
                current = repositories.jobs.get(running.id)
                if current.status is JobStatus.CANCEL_REQUESTED:
                    output.unlink(missing_ok=True)
                    self._cancel_if_requested(repositories, running.id)
        except kk.CancelledError:
            self._cancel_if_requested(repositories, running.id)
        except Exception:
            if repositories.jobs.get(running.id).status is JobStatus.CANCEL_REQUESTED:
                self._cancel_if_requested(repositories, running.id)
            else:
                self._fail_if_running(repositories, running.id)
        finally:
            self._finish_dispatch(repositories, dispatch_id, running.id, output)

    def _finish_dispatch(
        self, repositories: Repositories, dispatch_id: str, job_id: str, output: Path | None
    ) -> None:
        while True:
            current_dispatch = repositories.dispatches.get(dispatch_id)
            if current_dispatch.status == "done":
                return
            if repositories.finish_dispatch_if_not_cancellation_requested(current_dispatch):
                return
            if output is not None:
                output.unlink(missing_ok=True)
            self._cancel_if_requested(repositories, job_id)

    def _cancel_if_requested(self, repositories: Repositories, job_id: str) -> None:
        current = repositories.jobs.get(job_id)
        if current.status is JobStatus.CANCEL_REQUESTED:
            self._update(repositories, transition(current, Cancelled()), "cancelled")

    def _fail_if_running(self, repositories: Repositories, job_id: str) -> None:
        current = repositories.jobs.get(job_id)
        if current.status is JobStatus.RUNNING:
            self._update(repositories, transition(current, Failed()), "failed")


def run_dispatch(database_path: str, assets_root: str, dispatch_id: str, fixture_mode: bool) -> None:
    """Multiprocessing-safe worker entrypoint."""
    LocalJobRunner(database_path, assets_root, fixture_mode=fixture_mode).run(dispatch_id)
