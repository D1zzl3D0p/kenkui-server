import pytest

from kenkui_server.jobs.models import (
    Job,
    JobSpec,
    JobStatus,
    OutputSpec,
    SingleVoiceCasting,
    TtsSettings,
)
from kenkui_server.jobs.transitions import (
    CancelRequested,
    DispatchRequested,
    InvalidTransition,
    ProgressReported,
    transition,
)


def queued_job() -> Job:
    return Job(
        id="job-1",
        spec=JobSpec(
            source_id="asset-1",
            chapters=("chapter-a",),
            casting=SingleVoiceCasting(voice_id="en_US-amy"),
            tts=TtsSettings(),
            output=OutputSpec(path="/tmp/book.m4b"),
        ),
    )


def test_queued_cancel_is_terminal() -> None:
    assert transition(queued_job(), CancelRequested()).status is JobStatus.CANCELLED


def test_dispatch_then_progress_preserves_immutable_snapshot() -> None:
    queued = queued_job()
    running = transition(queued, DispatchRequested())
    progressed = transition(running, ProgressReported(stage="synthesis", completed=1, total=3))

    assert queued.status is JobStatus.QUEUED
    assert running.status is JobStatus.RUNNING
    assert progressed.progress.completed == 1
    assert progressed.progress.total == 3
    assert progressed.version == 2


def test_terminal_transition_has_stable_invalid_code() -> None:
    cancelled = transition(queued_job(), CancelRequested())

    with pytest.raises(InvalidTransition) as raised:
        transition(cancelled, DispatchRequested())

    assert raised.value.code == "invalid_transition.cancelled.dispatch_requested"
