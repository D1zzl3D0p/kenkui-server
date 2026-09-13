"""Admission ordering for local durable jobs."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

from kenkui_server.billing.pricing import credits_for_characters
from kenkui_server.compute.base import ProcessRunner
from kenkui_server.jobs.models import Dispatch, Job, JobSpec
from kenkui_server.storage.repositories import Repositories


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
            self._runner.start(dispatch.id)


class HostedDispatcher:
    """Admit credit-backed hosted work through one durable transaction."""

    def __init__(self, repositories: Any, runner: ProcessRunner) -> None:
        self._repositories: Any = repositories
        self._runner = runner

    def submit(
        self,
        spec: JobSpec,
        *,
        owner_id: UUID,
        account_id: str,
        normalized_speech_characters: int,
        idempotency_key: str | None = None,
    ) -> Job:
        credits = credits_for_characters(normalized_speech_characters)
        if credits < 1:
            raise ValueError("empty_speech")
        job = Job(str(uuid4()), spec)
        dispatch = Dispatch(str(uuid4()), job.id, "pending")
        admitted: Job = self._repositories.admit(
            job,
            dispatch,
            account_id=account_id,
            owner_id=str(owner_id),
            credits=credits,
            idempotency_key=hashlib.sha256(f"{owner_id}:{idempotency_key}".encode()).hexdigest()
            if idempotency_key is not None
            else None,
        )
        if admitted.id == job.id:
            self._runner.start(dispatch.id)
        return admitted

    def recover(self) -> None:
        """Reclaim incomplete hosted work after an interrupted worker attempt."""
        for dispatch in self._repositories.dispatches.list_incomplete():
            job = self._repositories.jobs.get(dispatch.job_id)
            if job.status.is_terminal:
                continue
            self._runner.start(dispatch.id)
