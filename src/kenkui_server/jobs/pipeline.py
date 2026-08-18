"""Translation from durable job intent to Kenkui's public pipeline API."""

from __future__ import annotations

import os

import kenkui as kk

from kenkui_server.jobs.models import JobSpec


def pipeline_from_job(spec: JobSpec, source: str | os.PathLike[str]) -> kk.Pipeline:
    """Reconstruct a fresh public Kenkui pipeline from one immutable job spec."""
    pipeline = kk.book(source).select_chapters(*spec.chapters)
    if spec.tts.normalize_text:
        pipeline = pipeline.normalize_text()
    return pipeline.assign_voice(spec.casting.voice_id).tts()
