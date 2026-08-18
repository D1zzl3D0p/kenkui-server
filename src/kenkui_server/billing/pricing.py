"""Stable, provider-neutral credit pricing."""

from __future__ import annotations

import math
import re
import unicodedata

CHARS_PER_CREDIT = 1_000


def normalized_speech_characters(text: str) -> int:
    """Count speech text after Unicode and whitespace normalization."""
    normalized = unicodedata.normalize("NFC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return len(normalized)


def credits_for_characters(character_count: int) -> int:
    """Charge whole credits; an empty synthesis does not reserve a credit."""
    if character_count < 0:
        raise ValueError("invalid_character_count")
    return math.ceil(character_count / CHARS_PER_CREDIT)


def credits_for_text(text: str) -> int:
    return credits_for_characters(normalized_speech_characters(text))
