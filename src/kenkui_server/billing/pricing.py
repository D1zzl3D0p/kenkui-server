"""Stable, provider-neutral credit pricing."""

from __future__ import annotations

import re
import unicodedata

CREDITS_PER_BOOK = 1_000


def normalized_speech_characters(text: str) -> int:
    """Count speech text after Unicode and whitespace normalization."""
    normalized = unicodedata.normalize("NFC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return len(normalized)


def credits_for_characters(character_count: int) -> int:
    """Charge a flat rate per conversion, independent of book length."""
    if character_count < 0:
        raise ValueError("invalid_character_count")
    return CREDITS_PER_BOOK if character_count else 0


def credits_for_text(text: str) -> int:
    return credits_for_characters(normalized_speech_characters(text))
