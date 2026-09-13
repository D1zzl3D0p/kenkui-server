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
