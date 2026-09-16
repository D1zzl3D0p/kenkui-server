"""Character casting is advertised only where it is configured."""

from __future__ import annotations

from kenkui_server.jobs.models import CharacterCasting, JobSpec, SingleVoiceCasting


def test_default_deployment_advertises_single_only(client) -> None:  # noqa: ANN001
    """A server with no model allowlist cannot cast characters."""
    modes = client.get("/v1/capabilities").json()["casting"]["modes"]
    assert modes == ["single"]


def test_single_voice_casting_still_round_trips() -> None:
    """The existing shape must survive the union."""
    spec = JobSpec(
        source_id="s",
        chapters=("ch1",),
        casting=SingleVoiceCasting("eponine"),
        tts=_tts(),
        output=_output(),
    )
    assert isinstance(spec.casting, SingleVoiceCasting)


def test_character_casting_requires_a_model() -> None:
    """Attribution cannot run without one, so an empty id is not a job."""
    for blank in ("", "  "):
        try:
            CharacterCasting(
                narrator_voice_id="eponine",
                unknown_voice_id="eponine",
                cast=(),
                method="gendered",
                model_id=blank,
            )
        except ValueError:
            continue
        message = f"blank model {blank!r} accepted"
        raise AssertionError(message)


def test_character_casting_defaults_unknown_to_the_narrator() -> None:
    casting = CharacterCasting(
        narrator_voice_id="eponine",
        unknown_voice_id="",
        cast=(),
        method="gendered",
        model_id="fake/model",
    )
    assert casting.unknown_voice_id == "eponine"


def test_the_cast_is_stored_in_a_stable_order() -> None:
    """Two requests naming the same cast differently are one job spec."""
    forward = CharacterCasting(
        narrator_voice_id="n",
        unknown_voice_id="n",
        cast=(("b", "vera"), ("a", "anna")),
        method="gendered",
        model_id="m",
    )
    assert forward.cast == (("a", "anna"), ("b", "vera"))


def _tts():  # noqa: ANN202
    from kenkui_server.jobs.models import TtsSettings

    return TtsSettings()


def _output():  # noqa: ANN202
    from kenkui_server.jobs.models import OutputSpec

    return OutputSpec("out.m4b")


def test_characters_advertised_only_with_an_allowlist() -> None:
    """Offering a mode the server would refuse is worse than not offering it."""
    from kenkui_server.config import local_capabilities

    assert local_capabilities().casting.modes == ["single"]
    assert local_capabilities().casting.models == []
    configured = local_capabilities(("fake/model",))
    assert configured.casting.modes == ["single", "characters"]
    assert configured.casting.models == ["fake/model"]


def test_an_unlisted_model_is_refused_before_a_job_exists() -> None:
    """Checked at admission, so no job is created the worker must abandon."""
    from fastapi import HTTPException

    from kenkui_server.api.jobs import _casting
    from kenkui_server.api.schemas import CastingRequest

    request = CastingRequest(narratorVoiceId="eponine", modelId="evil/model")
    try:
        _casting(request, ("fake/model",))
    except HTTPException as error:
        assert error.detail == "model_not_allowed"
        return
    message = "an unlisted model was accepted"
    raise AssertionError(message)


def test_a_request_naming_no_model_is_single_casting() -> None:
    from kenkui_server.api.jobs import _casting
    from kenkui_server.api.schemas import CastingRequest

    casting = _casting(CastingRequest(voiceId="eponine"), ())
    assert isinstance(casting, SingleVoiceCasting)


def test_a_request_naming_a_model_is_character_casting() -> None:
    from kenkui_server.api.jobs import _casting
    from kenkui_server.api.schemas import CastingRequest

    casting = _casting(
        CastingRequest(narratorVoiceId="eponine", modelId="fake/model"),
        ("fake/model",),
    )
    assert isinstance(casting, CharacterCasting)


def test_a_pre_character_row_still_loads() -> None:
    """Rows written before this feature carry no kind and must not break."""
    from kenkui_server.storage.repositories import _casting_from_row

    assert _casting_from_row({"voice_id": "eponine"}) == SingleVoiceCasting("eponine")


def test_character_casting_round_trips_through_a_row() -> None:
    from kenkui_server.storage.repositories import (
        _casting_from_row,
        _casting_to_row,
    )

    casting = CharacterCasting(
        narrator_voice_id="eponine",
        unknown_voice_id="paul",
        cast=(("javert", "charles"),),
        method="gendered",
        model_id="fake/model",
    )
    assert _casting_from_row(_casting_to_row(casting)) == casting
