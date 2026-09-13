"""Pure, deterministic transitions over immutable job snapshots."""

from __future__ import annotations

from dataclasses import dataclass, replace

from kenkui_server.jobs.models import Job, JobStatus, Progress


@dataclass(frozen=True, slots=True)
class DispatchRequested:
    """Request that a queued job becomes runnable."""


@dataclass(frozen=True, slots=True)
class CancelRequested:
    """Request cooperative cancellation of a non-terminal job."""


@dataclass(frozen=True, slots=True)
class Cancelled:
    """Record a worker's terminal observation of a cancellation request."""


@dataclass(frozen=True, slots=True)
class Completed:
    """Record successful completion of a running job."""


@dataclass(frozen=True, slots=True)
class Failed:
    """Record an execution failure of a running job."""


@dataclass(frozen=True, slots=True)
class ProgressReported:
    """Record a running job's presentation-neutral progress."""

    stage: str
    completed: int
    total: int

    @property
    def progress(self) -> Progress:
        return Progress(self.stage, self.completed, self.total)


JobTransitionEvent = (
    DispatchRequested | CancelRequested | Cancelled | Completed | Failed | ProgressReported
)


class InvalidTransition(ValueError):
    """A stable invalid-transition failure suitable for transport mapping."""

    def __init__(self, job: Job, event: JobTransitionEvent) -> None:
        self.code = f"invalid_transition.{job.status.value}.{_event_name(event)}"
        super().__init__(self.code)


def _event_name(event: JobTransitionEvent) -> str:
    if isinstance(event, DispatchRequested):
        return "dispatch_requested"
    if isinstance(event, CancelRequested):
        return "cancel_requested"
    if isinstance(event, Cancelled):
        return "cancelled"
    if isinstance(event, Completed):
        return "completed"
    if isinstance(event, Failed):
        return "failed"
    return "progress_reported"


def _next(job: Job, *, status: JobStatus | None = None, progress: Progress | None = None) -> Job:
    return replace(
        job,
        status=job.status if status is None else status,
        progress=job.progress if progress is None else progress,
        version=job.version + 1,
    )


def transition(job: Job, event: JobTransitionEvent) -> Job:
    """Return the next job snapshot or raise a stable invalid transition code."""
    if isinstance(event, CancelRequested):
        if job.status is JobStatus.QUEUED:
            return _next(job, status=JobStatus.CANCELLED)
        if job.status is JobStatus.RUNNING:
            return _next(job, status=JobStatus.CANCEL_REQUESTED)
    if isinstance(event, Cancelled) and job.status is JobStatus.CANCEL_REQUESTED:
        return _next(job, status=JobStatus.CANCELLED)
    if isinstance(event, DispatchRequested) and job.status is JobStatus.QUEUED:
        return _next(job, status=JobStatus.RUNNING, progress=Progress("running", 0, 0))
    if isinstance(event, Completed) and job.status is JobStatus.RUNNING:
        return _next(job, status=JobStatus.SUCCEEDED)
    if isinstance(event, Failed) and job.status is JobStatus.RUNNING:
        return _next(job, status=JobStatus.FAILED)
    if isinstance(event, ProgressReported) and job.status is JobStatus.RUNNING:
        return _next(job, progress=event.progress)
    raise InvalidTransition(job, event)
