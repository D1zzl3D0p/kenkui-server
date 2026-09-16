"""Hosted deployments expose only the voice set selected by release policy."""

from kenkui.voices.registry import CATALOG
from kenkui.voices.types import Voice

from kenkui_server.voice_catalog import select_hosted_voices


def test_vctk_set_includes_builtin_and_pack_voices() -> None:
    catalog = tuple(
        Voice(
            id=entry.id,
            name=entry.name,
            enabled=True,
            provenance=entry.origin_url,
            license_id=entry.license_id,
            commercial_use_allowed=entry.commercial_use_allowed,
        )
        for entry in CATALOG.values()
    )
    selected = select_hosted_voices("vctk", catalog)

    assert len(selected) == 59
    assert {
        "anna",
        "vera",
        "fantine",
        "charles",
        "paul",
        "eponine",
        "azelma",
        "george",
        "mary",
        "jane",
        "michael",
        "eve",
    } <= {voice.id for voice in selected}
    assert not {"jean", "cosette", "alba", "giovanni", "lola"} & {voice.id for voice in selected}
    assert all(voice.license_id == "CC-BY-4.0" for voice in selected)
    assert all("/vctk/" in (voice.provenance or "") for voice in selected)


def test_explicit_set_remains_available_for_private_deployments() -> None:
    voices = (
        Voice("one", "One", True, "local", "CC0-1.0", True),
        Voice("two", "Two", True, "local", "CC0-1.0", True),
    )
    assert [voice.id for voice in select_hosted_voices("two,one", voices)] == [
        "one",
        "two",
    ]
