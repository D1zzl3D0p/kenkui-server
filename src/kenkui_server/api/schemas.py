"""Transport-only models for the local versioned API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class AssetResponse(_Model):
    id: str
    format: str
    sha256: str


class ChapterResponse(_Model):
    id: str
    title: str
    speech_characters: int | None = Field(None, serialization_alias="speechCharacters")


class BookResponse(_Model):
    source_id: str = Field(serialization_alias="sourceId")
    title: str
    author: str
    chapters: list[ChapterResponse]


class VoiceResponse(_Model):
    id: str
    name: str
    language: str | None


class VoiceListResponse(_Model):
    items: list[VoiceResponse]


class CastingRequest(_Model):
    """Either a single narrator, or a character cast.

    voiceId alone means single casting. Naming a model means character
    casting, since attribution cannot run without one.
    """

    voice_id: str | None = Field(
        None, validation_alias="voiceId", serialization_alias="voiceId"
    )
    narrator_voice_id: str | None = Field(
        None,
        validation_alias="narratorVoiceId",
        serialization_alias="narratorVoiceId",
    )
    unknown_voice_id: str | None = Field(
        None,
        validation_alias="unknownVoiceId",
        serialization_alias="unknownVoiceId",
    )
    cast: dict[str, str] = Field(default_factory=dict)
    method: str = "gendered"
    model_id: str | None = Field(
        None, validation_alias="modelId", serialization_alias="modelId"
    )


class TtsRequest(_Model):
    normalize_text: bool = Field(True, validation_alias="normalizeText", serialization_alias="normalizeText")


class OutputRequest(_Model):
    format: str = "m4b"


class JobRequest(_Model):
    source_id: str = Field(validation_alias="sourceId", serialization_alias="sourceId")
    chapters: list[str]
    casting: CastingRequest
    tts: TtsRequest = TtsRequest()
    output: OutputRequest = OutputRequest()


class PreflightResponse(_Model):
    source_id: str = Field(serialization_alias="sourceId")
    normalized_characters: int = Field(serialization_alias="normalizedCharacters")
    valid: bool = True


class ProgressResponse(_Model):
    stage: str
    completed: int
    total: int


class JobResponse(_Model):
    id: str
    status: Literal["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"]
    progress: ProgressResponse


class JobListResponse(_Model):
    items: list[JobResponse]


class EventResponse(_Model):
    sequence: int
    type: str
    progress: ProgressResponse
