"""Translation from durable job intent to Kenkui's public pipeline API."""

from __future__ import annotations

import os

import kenkui as kk

from kenkui_server.jobs.models import JobSpec, SingleVoiceCasting


def pipeline_from_job(spec: JobSpec, source: str | os.PathLike[str]) -> kk.Pipeline:
    """Reconstruct a fresh public Kenkui pipeline from one immutable job spec."""
    pipeline = kk.book(source).select_chapters(*spec.chapters)
    # Normalization is intrinsic to Kenkui; legacy intent remains readable.
    pipeline = pipeline.metadata(
        title=spec.output.title,
        author=spec.output.author,
        cover="source" if spec.output.source_cover else None,
    )
    # Explicit lengths override the legacy chapter toggle, including zero.
    chapter_ms = spec.tts.chapter_pause_ms
    if chapter_ms is None:
        chapter_ms = 1500 if spec.tts.chapter_pauses else 0
    pauses = {
        "chapter_ms": chapter_ms,
        "heading_before_ms": spec.tts.heading_before_pause_ms,
        "heading_after_ms": spec.tts.heading_after_pause_ms,
        "paragraph_ms": spec.tts.paragraph_pause_ms,
        "line_ms": spec.tts.line_pause_ms,
        "scene_ms": spec.tts.scene_pause_ms,
    }
    if any(pauses.values()):
        pipeline = pipeline.pauses(**pauses)
    if (
        spec.tts.prepare_numbers
        or spec.tts.pronunciation_corrections
        or spec.tts.stutter_handling
    ):
        pipeline = pipeline.pronounce(
            numbers="conservative" if spec.tts.prepare_numbers else "off",
            builtin=spec.tts.pronunciation_corrections,
            stammer=spec.tts.stutter_handling,
            elongation=False,
        )
    casting = spec.casting
    if isinstance(casting, SingleVoiceCasting):
        return pipeline.assign_voice(casting.voice_id).tts()
    return (
        pipeline.infer_characters(model=casting.model_id, identity=None)
        .attribute_quotes(model=casting.model_id)
        .assign_voices(
            narrator=casting.narrator_voice_id,
            unknown=casting.unknown_voice_id,
            cast=dict(casting.cast),
            method=casting.method,
        )
        .tts()
    )
