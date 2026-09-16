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
    license_id: str | None = Field(None, serialization_alias="licenseId")
    voice_rights: str | None = Field(None, serialization_alias="voiceRights")


class VoiceListResponse(_Model):
    items: list[VoiceResponse]


class CastingRequest(_Model):
    """Either a single narrator, or a character cast.

    voiceId alone means single casting. Naming a model means character
    casting, since attribution cannot run without one.
    """

    voice_id: str | None = Field(None, validation_alias="voiceId", serialization_alias="voiceId")
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
    model_id: str | None = Field(None, validation_alias="modelId", serialization_alias="modelId")


class TtsRequest(_Model):
    normalize_text: bool = Field(
        True,
        validation_alias="normalizeText",
        serialization_alias="normalizeText",
        deprecated=True,
        description="Legacy field. Kenkui always normalizes source text.",
    )


class OutputRequest(_Model):
    format: str = "m4b"
    title: str | None = Field(None, min_length=1, max_length=500)
    author: str | None = Field(None, min_length=1, max_length=500)
    source_cover: bool = Field(True, alias="sourceCover")


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
    estimated_credits: int | None = Field(None, alias="estimatedCredits")
    available_credits: int | None = Field(None, alias="availableCredits")


class ProgressResponse(_Model):
    stage: str
    completed: int
    total: int


class JobResponse(_Model):
    source_cover: bool | None = Field(None, alias="sourceCover")
    source_id: str | None = Field(None, alias="sourceId")
    title: str | None = None
    author: str | None = None
    narrator_voice_id: str | None = Field(None, alias="narratorVoiceId")
    casting_mode: str | None = Field(None, alias="castingMode")
    failure: dict[str, str] | None = None
    id: str
    status: Literal["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"]
    progress: ProgressResponse


class JobListResponse(_Model):
    items: list[JobResponse]


class EventResponse(_Model):
    sequence: int
    type: str
    progress: ProgressResponse
