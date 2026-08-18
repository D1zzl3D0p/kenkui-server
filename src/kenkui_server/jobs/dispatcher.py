"""Admission ordering for local durable jobs."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from kenkui_server.compute.base import ProcessRunner
from kenkui_server.jobs.models import Dispatch, Job, JobSpec
from kenkui_server.storage.repositories import Repositories, StaleWriteError


class Dispatcher:
    """Commit job truth before asking a process runner to execute it."""

    def __init__(self, repositories: Repositories, runner: ProcessRunner) -> None:
        self._repositories = repositories
        self._runner = runner

    def submit(self, spec: JobSpec, *, idempotency_key: str | None = None) -> Job:
        """Atomically persist a queued job/dispatch, then start only a new dispatch."""
        job = Job(str(uuid4()), spec)
        dispatch = Dispatch(str(uuid4()), job.id, "pending")
        admitted = self._repositories.create_job_and_dispatch(
            job, dispatch, idempotency_key=idempotency_key
        )
        if admitted.id == job.id:
            self._runner.start(dispatch.id)
        return admitted

    def recover(self) -> None:
        """Reclaim incomplete work so a dead local worker cannot strand its Job."""
        for dispatch in self._repositories.dispatches.list_incomplete():
            job = self._repositories.jobs.get(dispatch.job_id)
            if job.status.is_terminal:
                continue
            if dispatch.status == "running":
                try:
                    self._repositories.dispatches.update(
                        replace(dispatch, status="pending", version=dispatch.version + 1),
                        expected_version=dispatch.version,
                    )
                except StaleWriteError:
                    continue
            self._runner.start(dispatch.id)
