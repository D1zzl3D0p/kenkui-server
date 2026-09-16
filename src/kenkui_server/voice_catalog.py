"""Hosted voice selection by dataset metadata; not a legal rights clearance."""

from __future__ import annotations

from collections.abc import Iterable

from kenkui.voices.registry import load_pack
from kenkui.voices.types import Voice

VCTK_VOICE_SET = "vctk"
_VCTK_ORIGIN = "hf://kyutai/tts-voices/vctk/"


def select_hosted_voices(selector: str, catalog: Iterable[Voice]) -> tuple[Voice, ...]:
    """Resolve a dataset-restricted set or an explicit list, without asserting consent."""
    available = tuple(voice for voice in catalog if voice.enabled)
    if selector.strip().lower() == VCTK_VOICE_SET:
        pack_ids = {entry.id for entry in load_pack().entries if entry.dataset == VCTK_VOICE_SET}
        selected = tuple(
            voice
            for voice in available
            if voice.id in pack_ids
            and voice.license_id == "CC-BY-4.0"
            and (voice.provenance or "").startswith(_VCTK_ORIGIN)
        )
        if not selected:
            raise ValueError("the VCTK hosted voice set is unavailable")
        return selected

    requested = {voice_id.strip() for voice_id in selector.split(",") if voice_id.strip()}
    if not requested:
        raise ValueError("configured voice set is empty")
    selected = tuple(voice for voice in available if voice.id in requested)
    if {voice.id for voice in selected} != requested:
        raise ValueError("configured voice is missing or disabled")
    return selected
