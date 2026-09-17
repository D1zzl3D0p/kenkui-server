"""Immutable domain values for local audiobook jobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


def _required(value: str, code: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(code)
    return normalized


@dataclass(frozen=True, slots=True)
class SingleVoiceCasting:
    """The only supported local casting intent."""

    voice_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "voice_id", _required(self.voice_id, "invalid_voice_id"))


@dataclass(frozen=True, slots=True)
class CharacterCasting:
    """Character-voice casting intent for one job.

    unknown_voice_id falls back to the narrator, matching the library: a line
    nobody could place sounds like narration rather than vanishing.
    """

    narrator_voice_id: str
    unknown_voice_id: str
    cast: tuple[tuple[str, str], ...]
    method: str
    model_id: str

    def __post_init__(self) -> None:
        narrator = _required(self.narrator_voice_id, "invalid_voice_id")
        object.__setattr__(self, "narrator_voice_id", narrator)
        object.__setattr__(
            self,
            "unknown_voice_id",
            self.unknown_voice_id.strip() or narrator,
        )
        object.__setattr__(self, "model_id", _required(self.model_id, "invalid_model_id"))
        object.__setattr__(self, "method", _required(self.method, "invalid_method"))
        # Sorted so two requests naming the same cast in different orders are
        # one job spec, and therefore one idempotency key.
        object.__setattr__(
            self,
            "cast",
            tuple(
                sorted(
                    (
                        _required(character, "invalid_character_id"),
                        _required(voice, "invalid_voice_id"),
                    )
                    for character, voice in self.cast
                )
            ),
        )


Casting = SingleVoiceCasting | CharacterCasting


@dataclass(frozen=True, slots=True)
class TtsSettings:
    """Deterministic, provider-independent synthesis intent."""

    normalize_text: bool = True
    chapter_pauses: bool = False
    prepare_numbers: bool = False
    pronunciation_corrections: bool = False
    stutter_handling: bool = False
    chapter_pause_ms: int | None = None
    heading_before_pause_ms: int = 0
    heading_after_pause_ms: int = 0
    paragraph_pause_ms: int = 0
    line_pause_ms: int = 0

    def __post_init__(self) -> None:
        # False defaults preserve jobs stored before speech settings existed.
        for name in (
            "normalize_text", "chapter_pauses", "prepare_numbers",
            "pronunciation_corrections", "stutter_handling",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"invalid_{name}")

        for name in (
            "chapter_pause_ms", "heading_before_pause_ms", "heading_after_pause_ms",
            "paragraph_pause_ms", "line_pause_ms",
        ):
            value = getattr(self, name)
            if name == "chapter_pause_ms" and value is None:
                continue
            if type(value) is not int or not 0 <= value <= 60_000:
                raise ValueError(f"invalid_{name}")


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """Requested local M4B publication location."""

    path: str
    title: str | None = None
    author: str | None = None
    source_cover: bool = True

    def __post_init__(self) -> None:
        path = _required(self.path, "invalid_output_path")
        if not path.lower().endswith(".m4b"):
            raise ValueError("invalid_output_path")
        object.__setattr__(self, "path", path)


@dataclass(frozen=True, slots=True)
class JobSpec:
    """Immutable execution intent independent from server persistence rows."""

    source_id: str
    chapters: tuple[str, ...]
    casting: Casting
    tts: TtsSettings
    output: OutputSpec

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required(self.source_id, "invalid_source_id"))
        chapter_ids = tuple(
            _required(chapter_id, "invalid_chapter_id") for chapter_id in self.chapters
        )
        if not chapter_ids:
            raise ValueError("empty_chapter_selection")
        if len(chapter_ids) != len(set(chapter_ids)):
            raise ValueError("duplicate_chapter_id")
        object.__setattr__(self, "chapters", chapter_ids)


class JobStatus(StrEnum):
    """Persisted state of a local job."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


@dataclass(frozen=True, slots=True)
class Progress:
    """A bounded, presentation-neutral execution progress snapshot."""

    stage: str
    completed: int
    total: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _required(self.stage, "invalid_progress_stage"))
        if isinstance(self.completed, bool) or isinstance(self.total, bool):
            raise ValueError("invalid_progress")
        if self.completed < 0 or self.total < 0 or self.completed > self.total:
            raise ValueError("invalid_progress")


@dataclass(frozen=True, slots=True)
class Job:
    """The authoritative snapshot for one local job."""

    id: str
    spec: JobSpec
    status: JobStatus = JobStatus.QUEUED
    version: int = 0
    progress: Progress = Progress("queued", 0, 0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "invalid_job_id"))
        if isinstance(self.version, bool) or self.version < 0:
            raise ValueError("invalid_job_version")


@dataclass(frozen=True, slots=True)
class JobEvent:
    """An ordered durable record of a job state observation."""

    job_id: str
    sequence: int
    event_type: str
    progress: Progress

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _required(self.job_id, "invalid_job_id"))
        object.__setattr__(self, "event_type", _required(self.event_type, "invalid_event_type"))
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("invalid_event_sequence")


@dataclass(frozen=True, slots=True)
class Asset:
    """A registered local source asset."""

    id: str
    path: str
    sha256: str
    format: str


@dataclass(frozen=True, slots=True)
class InspectedChapter:
    """Stable chapter identity captured during source inspection."""

    id: str
    title: str


@dataclass(frozen=True, slots=True)
class Inspection:
    """A durable source inspection snapshot."""

    source_id: str
    title: str
    author: str
    chapters: tuple[InspectedChapter, ...]


@dataclass(frozen=True, slots=True)
class Dispatch:
    """A durable local dispatch claim; no worker behavior is implied."""

    id: str
    job_id: str
    status: str
    version: int = 0


@dataclass(frozen=True, slots=True)
class Artifact:
    """A published local output artifact."""

    id: str
    job_id: str
    path: str
    format: str
