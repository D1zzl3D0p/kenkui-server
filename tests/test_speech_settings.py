import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from kenkui_server.api.jobs import _spec
from kenkui_server.api.schemas import JobRequest
from kenkui_server.jobs.models import CharacterCasting, TtsSettings
from kenkui_server.jobs.pipeline import pipeline_from_job
from kenkui_server.storage.repositories import _decode_spec, _encode_spec


def request(**tts: object) -> JobRequest:
    return JobRequest.model_validate({
        "sourceId": "source", "chapters": ["one", "two"],
        "casting": {"voiceId": "narrator"}, "tts": tts,
    })


@pytest.mark.parametrize("full_cast", [False, True])
def test_speech_policy_survives_storage_and_applies_to_both_modes(full_cast: bool) -> None:
    from kenkui._domain.operations import Pauses, SpokenForm

    spec = _spec(request(chapterPauses=True, prepareNumbers=True,
                         pronunciationCorrections=True, stutterHandling=False))
    if full_cast:
        spec = replace(spec, casting=CharacterCasting(
            "narrator", "narrator", (), "gendered", "test/model",
        ))
    restored = _decode_spec(_encode_spec(spec))
    assert restored == spec
    pipeline = pipeline_from_job(restored, "book.epub")
    assert next(op for op in pipeline.operations if isinstance(op, Pauses)) == Pauses(
        chapter_ms=1500,
    )
    speech = next(op for op in pipeline.operations if isinstance(op, SpokenForm))
    assert speech.numbers == "conservative"
    assert speech.builtin_lexicon is True
    assert dict(speech.features) == {"elongation": False, "stammer": False}


def test_legacy_jobs_do_not_gain_new_behavior() -> None:
    from kenkui._domain.operations import Pauses, SpokenForm

    raw = json.loads(_encode_spec(_spec(request())))
    raw["tts"] = {"normalize_text": True}
    restored = _decode_spec(json.dumps(raw))
    assert restored.tts == TtsSettings()
    assert not any(isinstance(op, (Pauses, SpokenForm))
                   for op in pipeline_from_job(restored, "book.epub").operations)


@pytest.mark.parametrize("field", ["prepareNumbers", "pronunciationCorrections", "stutterHandling"])
def test_speech_toggles_are_independent(field: str) -> None:
    from kenkui._domain.operations import SpokenForm

    pipeline = pipeline_from_job(_spec(request(**{field: True})), "book.epub")
    speech = next(op for op in pipeline.operations if isinstance(op, SpokenForm))
    assert speech.numbers == ("conservative" if field == "prepareNumbers" else "off")
    assert speech.builtin_lexicon == (field == "pronunciationCorrections")
    assert dict(speech.features) == {
        "elongation": False, "stammer": field == "stutterHandling",
    }


def test_settings_reject_non_booleans() -> None:
    with pytest.raises(ValidationError):
        request(chapterPauses="false")


@pytest.mark.parametrize("full_cast", [False, True])
def test_custom_pause_lengths_round_trip_and_reach_pipeline(full_cast: bool) -> None:
    from kenkui._domain.operations import Pauses

    spec = _spec(request(chapterPauseMs=2200, headingBeforePauseMs=150,
                         headingAfterPauseMs=600, paragraphPauseMs=250, linePauseMs=100,
                         scenePauseMs=900))
    if full_cast:
        spec = replace(spec, casting=CharacterCasting(
            "narrator", "narrator", (), "gendered", "test/model",
        ))
    restored = _decode_spec(_encode_spec(spec))
    assert restored == spec
    pipeline = pipeline_from_job(restored, "book.epub")
    assert next(op for op in pipeline.operations if isinstance(op, Pauses)) == Pauses(
        chapter_ms=2200, heading_before_ms=150, heading_after_ms=600,
        paragraph_ms=250, line_ms=100, scene_ms=900,
    )


def test_scene_pause_is_independent_of_every_other_tier() -> None:
    from kenkui._domain.operations import Pauses

    spec = _spec(request(scenePauseMs=900))
    pipeline = pipeline_from_job(spec, "book.epub")
    assert next(op for op in pipeline.operations if isinstance(op, Pauses)) == Pauses(
        scene_ms=900,
    )


def test_chapter_pause_implies_no_scene_pause() -> None:
    from kenkui._domain.operations import Pauses

    spec = _spec(request(chapterPauses=True))
    pauses = next(op for op in pipeline_from_job(spec, "book.epub").operations
                  if isinstance(op, Pauses))
    assert pauses.scene_ms == 0


def test_zero_explicitly_overrides_old_chapter_toggle() -> None:
    from kenkui._domain.operations import Pauses

    spec = _spec(request(chapterPauses=True, chapterPauseMs=0))
    assert not any(isinstance(op, Pauses)
                   for op in pipeline_from_job(spec, "book.epub").operations)


@pytest.mark.parametrize("field", ["chapterPauseMs", "headingBeforePauseMs", "headingAfterPauseMs",
                                   "paragraphPauseMs", "linePauseMs", "scenePauseMs"])
@pytest.mark.parametrize("value", [-1, 60001, 1.5, True, "1500"])
def test_pause_lengths_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.parametrize("value", [0, 60000])
def test_pause_lengths_accept_bounds(value: int) -> None:
    assert _spec(request(chapterPauseMs=value)).tts.chapter_pause_ms == value
