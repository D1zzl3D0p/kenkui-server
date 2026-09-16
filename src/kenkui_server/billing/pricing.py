"""Stable, provider-neutral credit pricing."""

from __future__ import annotations

import os
import re
import unicodedata

CREDITS_PER_DOLLAR = 100


def normalized_speech_characters(text: str) -> int:
    """Count speech text after Unicode and whitespace normalization."""
    normalized = unicodedata.normalize("NFC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return len(normalized)


def credits_for_characters(character_count: int, *, multivoice: bool = False) -> int:
    """Quote 2x estimated compute cost, plus 50% for character casting.

    The initial $1.26 / million speech characters is calibrated to the reported
    $1.50 Dune render (1,189,736 characters). Round up to a whole credit.
    This is an estimate, not measured per-job provider billing.
    """
    if character_count < 0:
        raise ValueError("invalid_character_count")
    cost = int(os.environ.get("KENKUI_ESTIMATED_COST_CENTS_PER_MILLION", "126"))
    if cost < 1:
        raise ValueError("invalid_estimated_processing_cost")
    numerator = character_count * cost * (3 if multivoice else 2)
    return (numerator + 999_999) // 1_000_000


def credits_for_text(text: str) -> int:
    return credits_for_characters(normalized_speech_characters(text))
