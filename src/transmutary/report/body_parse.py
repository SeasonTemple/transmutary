"""Bilingual body parser — split LLM output into EN + ZH sections (KTD2).

The LLM produces a single response containing both English and Chinese text
separated by ``<!-- BILINGUAL:SPLIT -->``. This module extracts the two
sections. When the marker is absent, the entire text is treated as English
only (graceful degradation).
"""

from __future__ import annotations

import re

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


# --- Robust revision parser (for CHEAP-tier critique-refine output) ----------
# A weaker model ignores "output ONLY the revised text": it wraps the result in
# a "## DRAFT REPORT (revised)" heading, appends a "## Draft Revision Notes"
# meta section, and sometimes emits the two language sections in the WRONG order
# (中文 before English). parse_bilingual() would mis-split all of that into the
# wrong slots. parse_bilingual_revision() strips the meta noise and classifies
# each chunk by its CJK ratio, so the EN/ZH slots are correct regardless of the
# order the model chose.

# A heading line introducing the wrapper, e.g. "## DRAFT REPORT (revised)".
_NOISE_HEADING_RE = re.compile(
    r"^\s*#{1,6}.*\b(?:DRAFT|REVISED)\s+REPORT\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
# The revision-notes meta section and everything after it (it is commentary
# about the edit, never report content).
_NOTES_TAIL_RE = re.compile(
    r"\n\s*#{1,6}\s*(?:draft\s+)?revision\s+notes\b.*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _cjk_ratio(text: str) -> float:
    """Fraction of non-whitespace chars that are CJK ideographs."""
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return 0.0
    cjk = sum(1 for c in visible if "一" <= c <= "鿿")
    return cjk / len(visible)


def _clean_chunk(chunk: str) -> str:
    """Drop wrapper headings and stray horizontal rules from one language chunk."""
    chunk = _NOISE_HEADING_RE.sub("", chunk)
    chunk = chunk.strip()
    chunk = re.sub(r"^-{3,}\s*", "", chunk)  # leading ---
    chunk = re.sub(r"\s*-{3,}$", "", chunk)  # trailing ---
    return chunk.strip()


def parse_bilingual_revision(
    raw: str, *, fallback_en: str = "", fallback_zh: str = ""
) -> tuple[str, str]:
    """Split a noisy critique-refine result into clean ``(en, zh)`` summaries.

    Robust to the failure modes a CHEAP model exhibits: a wrapping "DRAFT
    REPORT (revised)" heading, a trailing "Draft Revision Notes" meta section,
    and reversed section order. Chunks are classified by CJK ratio (not by
    position), so a 中文-first response still lands in the right slot. An empty
    or all-noise side falls back to the supplied original draft for that
    language, so refine never blanks a slot (KTD-D parity).
    """
    text = _NOTES_TAIL_RE.sub("", raw or "")
    if _SPLIT_MARKER in text:
        a, _, b = text.partition(_SPLIT_MARKER)
        chunks = [_clean_chunk(a), _clean_chunk(b)]
    else:
        chunks = [_clean_chunk(text)]

    en_part = ""
    zh_part = ""
    for chunk in chunks:
        if not chunk:
            continue
        if _cjk_ratio(chunk) >= 0.15:
            zh_part = chunk if not zh_part else f"{zh_part}\n\n{chunk}"
        else:
            en_part = chunk if not en_part else f"{en_part}\n\n{chunk}"
    return (en_part or fallback_en, zh_part or fallback_zh)
