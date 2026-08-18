from dataclasses import FrozenInstanceError

import pytest

from kenkui_server.jobs.models import JobSpec, OutputSpec, SingleVoiceCasting, TtsSettings


def single_voice_spec() -> JobSpec:
    return JobSpec(
        source_id="asset-9d4d",
        chapters=("chapter-a", "chapter-b"),
        casting=SingleVoiceCasting(voice_id="en_US-amy"),
        tts=TtsSettings(normalize_text=True),
        output=OutputSpec(path="/tmp/book.m4b"),
    )


def test_job_spec_keeps_stable_chapter_ids_and_is_immutable() -> None:
    spec = single_voice_spec()

    assert spec.chapters == ("chapter-a", "chapter-b")
    with pytest.raises(FrozenInstanceError):
        spec.source_id = "another-asset"  # type: ignore[misc]


def test_job_spec_rejects_duplicate_or_blank_chapter_ids() -> None:
    common = dict(
        source_id="asset-9d4d",
        casting=SingleVoiceCasting(voice_id="en_US-amy"),
        tts=TtsSettings(),
        output=OutputSpec(path="/tmp/book.m4b"),
    )

    with pytest.raises(ValueError, match="duplicate_chapter_id"):
        JobSpec(chapters=("chapter-a", "chapter-a"), **common)
    with pytest.raises(ValueError, match="invalid_chapter_id"):
        JobSpec(chapters=("",), **common)
