"""Bilingual body parser — split LLM output into EN + ZH sections (KTD2).

The LLM produces a single response containing both English and Chinese text
separated by ``<!-- BILINGUAL:SPLIT -->``. This module extracts the two
sections. When the marker is absent, the entire text is treated as English
only (graceful degradation).
"""

from __future__ import annotations

_SPLIT_MARKER = "<!-- BILINGUAL:SPLIT -->"


def parse_bilingual(raw: str) -> tuple[str, str | None]:
    """Split a raw LLM response into (english, chinese) bodies.

    Returns ``(raw, None)`` when the split marker is not found.
    Returns ``(en, zh)`` where either may be empty when the marker IS found.
    """
    if _SPLIT_MARKER not in raw:
        return (raw, None)
    en, _, zh = raw.partition(_SPLIT_MARKER)
    return (en, zh)
