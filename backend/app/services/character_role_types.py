"""Canonical character-role vocabulary shared by every write path."""
from __future__ import annotations

from typing import Final


CHARACTER_ROLE_TYPES: Final[tuple[str, ...]] = (
    "protagonist",
    "supporting",
    "antagonist",
    "mentor",
    "other",
)

def normalize_character_role_type(
    value: object,
    *,
    default: str | None = "other",
) -> str | None:
    """Validate a structured role enum without interpreting natural language."""

    text = str(value or "").strip().casefold()
    if text == "merged_alias":
        return text
    return text if text in CHARACTER_ROLE_TYPES else default
