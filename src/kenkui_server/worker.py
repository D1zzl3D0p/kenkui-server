"""One local worker attempt reconstructed entirely from durable state."""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from typing import Any
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

    def __init__(
        self,
        database_path: str | Path,
        assets_root: str | Path,
        *,
        fixture_mode: bool = False,
        lease: tuple[str, str] | None = None,
        render_workers: int = 1,
    ) -> None:
        self._database_path = Path(database_path)
        self._assets_root = Path(assets_root)
        self._fixture_mode = fixture_mode
        self._lease = lease
        self._lease_lost = Event()
        self._render_workers = render_workers

    def _database(self) -> Any:
        return Database(self._database_path)

    def _repositories(self, database: Any) -> Any:
        return Repositories(database)

    def _store(self) -> Any:
        return AssetStore(self._assets_root)

    def _publish(self, store: Any, job_id: str, output: Path) -> str:
        return str(output)

    def _checkpoints(self, job_id: str) -> AbstractContextManager[None]:
        return nullcontext()

    def run(self, dispatch_id: str) -> None:
        database = self._database()
        stop = Event()
        heartbeat: Thread | None = None
        if self._lease is not None:
            lease = self._lease

            def renew() -> None:
                heartbeat_database = self._database()
                try:
                    repositories = self._repositories(heartbeat_database)
                    while not stop.wait(5):
                        if not repositories.renew_execution(*lease):
                            self._lease_lost.set()
                            return
                except Exception:
                    self._lease_lost.set()
                    logging.getLogger(__name__).exception("worker_heartbeat_failed")
                finally:
                    heartbeat_database.close()

            heartbeat = Thread(target=renew, daemon=True)
            heartbeat.start()
        try:
            self._run(self._repositories(database), self._store(), dispatch_id)
        except StaleWriteError:
            logging.getLogger(__name__).info(
                "worker_lease_lost", extra={"dispatch_id": dispatch_id}
            )
        finally:
            stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=10)
            if self._lease is not None:
                self._repositories(database).release_execution(*self._lease)
            database.close()

    def _update(
        self,
        repositories: Repositories,
        job: Job,
        event_type: str,
        artifact: Artifact | None = None,
        failure: tuple[str, str] | None = None,
    ) -> bool:
        """Apply a transition and append its event in one guarded transaction."""
        try:
            repositories.update_job_and_append_event(
                job,
                expected_version=job.version - 1,
                event_type=event_type,
                artifact=artifact,
                lease=self._lease,
                failure=failure,
            )
        except StaleWriteError:
            return False
        return True

    def _notify_completed(self, job: Job) -> None:
        """Tell a job's owner it finished.

        A local server has no addressable owner and no mail provider, so this
        is a no-op that hosted composition overrides.
        """

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
            if self._lease is not None:
                # Attempts currently restart synthesis; do not display the
                # previous attempt's completed chapters as current progress.
                running = transition(job, ProgressReported("retrying", 0, job.progress.total))
                if not self._update(repositories, running, "progress"):
                    return
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
            repositories.assets.get(running.spec.source_id)
            output = store.artifact_path(
                f"{running.id}.{self._lease[1]}" if self._lease else running.id
            )
            if self._fixture_mode:
                output.write_bytes(b"KENKUI-FIXTURE-M4B\n")
            else:
                cancellation = kk.CancellationToken()
                stop_polling = Event()

                def poll_cancellation() -> None:
                    polling_database = self._database()
                    try:
                        polling_repositories = self._repositories(polling_database)
                        while not stop_polling.wait(0.05):
                            if (
                                self._lease_lost.is_set()
                                or polling_repositories.jobs.get(running.id).status
                                is JobStatus.CANCEL_REQUESTED
                            ):
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
                    if (
                        stage == current.progress.stage and completed < current.progress.completed
                    ) or total < completed:
                        return
                    next_job = transition(current, ProgressReported(stage, completed, total))
                    self._update(repositories, next_job, "progress")

                try:
                    with (
                        self._checkpoints(running.id),
                        store.materialize_source(running.spec.source_id) as source,
                    ):
                        pipeline_from_job(running.spec, source).write(
                            output,
                            on_event=on_event,
                            cancel=cancellation,
                            workers=self._render_workers,
                            overwrite=True,
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
            artifact = Artifact(
                str(uuid4()), completed.id, self._publish(store, completed.id, output), "m4b"
            )
            if self._update(repositories, completed, "completed", artifact):
                # Only a committed completion notifies, so a lost race stays silent.
                self._notify_completed(completed)
            else:
                current = repositories.jobs.get(running.id)
                if current.status is JobStatus.CANCEL_REQUESTED:
                    output.unlink(missing_ok=True)
                    self._cancel_if_requested(repositories, running.id)
        except kk.CancelledError:
            self._cancel_if_requested(repositories, running.id)
        except StaleWriteError:
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("job_failed", extra={"job_id": running.id})
            if repositories.jobs.get(running.id).status is JobStatus.CANCEL_REQUESTED:
                self._cancel_if_requested(repositories, running.id)
            else:
                self._fail_if_running(repositories, running.id, error)
        finally:
            self._finish_dispatch(repositories, dispatch_id, running.id, output)

    def _finish_dispatch(
        self, repositories: Repositories, dispatch_id: str, job_id: str, output: Path | None
    ) -> None:
        while True:
            current = repositories.jobs.get(job_id)
            # An interrupted attempt is not a completed job. In particular,
            # Modal preemption unwinds this finally block via BaseException.
            if not current.status.is_terminal and current.status is not JobStatus.CANCEL_REQUESTED:
                return
            current_dispatch = repositories.dispatches.get(dispatch_id)
            if current_dispatch.status == "done":
                return
            if repositories.finish_dispatch_if_not_cancellation_requested(
                current_dispatch, lease=self._lease
            ):
                return
            if output is not None:
                output.unlink(missing_ok=True)
            self._cancel_if_requested(repositories, job_id)

    def _cancel_if_requested(self, repositories: Repositories, job_id: str) -> None:
        current = repositories.jobs.get(job_id)
        if current.status is JobStatus.CANCEL_REQUESTED:
            self._update(repositories, transition(current, Cancelled()), "cancelled")

    def _fail_if_running(self, repositories: Repositories, job_id: str, error: Exception) -> None:
        current = repositories.jobs.get(job_id)
        if current.status is JobStatus.RUNNING:
            self._update(
                repositories,
                transition(current, Failed()),
                "failed",
                failure=(error.code.value, str(error))
                if isinstance(error, kk.KenkuiError)
                else (
                    "execution_failed",
                    "Audiobook creation failed. Please retry or contact support with the job ID.",
                ),
            )


def run_dispatch(
    database_path: str, assets_root: str, dispatch_id: str, fixture_mode: bool
) -> None:
    """Multiprocessing-safe worker entrypoint."""
    LocalJobRunner(database_path, assets_root, fixture_mode=fixture_mode).run(dispatch_id)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("assets")
    parser.add_argument("dispatch")
    parser.add_argument("token")
    parser.add_argument("workers", type=int)
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    LocalJobRunner(
        args.database,
        args.assets,
        fixture_mode=args.fixture,
        lease=(args.dispatch, args.token),
        render_workers=args.workers,
    ).run(args.dispatch)
