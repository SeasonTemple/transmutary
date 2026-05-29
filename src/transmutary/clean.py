"""Structural cleaning before the LLM (U10, R17, KTD3-adjacent).

``clean.py`` runs DETERMINISTIC structural checks on candidate content BEFORE any
LLM sees it (R17 — cleaning precedes the model). MVP scope (KTD6):

  * **Structural checks first** — drop content that is stale (older than a
    freshness window relative to the event), unreachable (no usable URL/body), or
    structurally empty. These are cheap, deterministic gates that keep junk out of
    the (expensive, untrusted) LLM payload.
  * **Relevance trimming = paragraph rules (MVP)** — chunk-level *semantic*
    relevance is deferred with L2 (KTD6). Here we split content into paragraphs
    and keep paragraphs that mention the observation target (repo / its
    dependency names / fault keywords). We **explicitly accept** that this narrows
    less than R17's chunk-level filter, so single-diagnose token cost runs higher
    and the L3 daily cap must be sized for a fuller payload (KTD7). An optional
    cheap-model chunk pre-filter is left as a hook but not wired in MVP.

Nothing here calls an LLM. The cleaned text is what ``diagnose.py`` (U10) hands to
``llm.py`` (U14) as the untrusted DATA block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Default freshness window: content whose timestamp is older than this relative to
# the event anchor is considered stale and dropped before the LLM (R17). Shipped
# default; configurable later.
DEFAULT_STALENESS_WINDOW_SECONDS = 90 * 24 * 60 * 60  # 90 days

# Fault-signal keywords reused for paragraph relevance (kept independent of dedup
# buckets so trimming stays a local, auditable rule).
_RELEVANCE_KEYWORDS = (
    "error",
    "down",
    "outage",
    "timeout",
    "500",
    "502",
    "503",
    "504",
    "fail",
    "crash",
    "panic",
    "vulnerab",
    "cve",
    "ghsa",
    "advisory",
    "malware",
    "deprecat",
    "breaking",
    "regression",
    "挂了",
    "超时",
    "宕机",
    "不可用",
    "崩溃",
    "漏洞",
)

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")


class StaleContentError(Exception):
    """Internal marker — content is too old for the freshness window (R17)."""


@dataclass
class CleanInput:
    """One candidate piece of content to clean before the LLM."""

    repo: str
    text: str
    url: str = ""
    ts: str = ""  # ISO-8601 timestamp of the content
    # Identifiers/keywords that make a paragraph relevant (repo, dep names, etc.).
    relevance_terms: list[str] = field(default_factory=list)


@dataclass
class CleanResult:
    """Outcome of cleaning one candidate."""

    repo: str
    text: str  # trimmed, relevant text (may be empty if everything dropped)
    url: str
    ts: str
    kept: bool  # passed the structural gates (not stale / unreachable / empty)
    reason: str = ""  # why dropped, when not kept


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None if unparseable."""
    if not ts:
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_stale(ts: str, anchor: str | datetime | None, *, window_secs: float) -> bool:
    """Return True if ``ts`` is older than ``window_secs`` before the anchor.

    The anchor is the event time (deterministic; no wall clock dependency in
    tests). Unparseable timestamps are treated as NOT stale — we do not silently
    drop content just because its timestamp is malformed (reachability handles
    truly empty content).
    """
    content_dt = _parse_iso(ts)
    if content_dt is None:
        return False
    if isinstance(anchor, datetime):
        anchor_dt: datetime | None = anchor
    else:
        anchor_dt = _parse_iso(anchor) if anchor else None
    if anchor_dt is None:
        anchor_dt = datetime.now(timezone.utc)
    return (anchor_dt - content_dt).total_seconds() > window_secs


def is_reachable(item: CleanInput) -> bool:
    """Structural reachability: there must be usable body text.

    A candidate with neither body text is treated as unreachable/empty and kept
    out of the LLM payload (R17). A URL alone (no body) is not enough — the model
    cannot fetch it (no network in the LLM path).
    """
    return bool(item.text and item.text.strip())


def trim_relevant_paragraphs(text: str, terms: list[str]) -> str:
    """MVP paragraph-rule relevance trimming (NOT chunk-level semantics, KTD6).

    Keep paragraphs that mention the observation target (any of ``terms``) or a
    fault-signal keyword. If nothing matches, fall back to the whole text rather
    than emptying it — we would rather pay extra tokens than silently lose a
    relevant signal a coarse rule missed (explicitly accepted, KTD7).
    """
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text or "") if p.strip()]
    if not paragraphs:
        return (text or "").strip()
    needles = [t.lower() for t in terms if t] + list(_RELEVANCE_KEYWORDS)
    kept = [p for p in paragraphs if any(n in p.lower() for n in needles)]
    if not kept:
        # Coarse rule matched nothing — accept higher token cost over data loss.
        return "\n\n".join(paragraphs)
    return "\n\n".join(kept)


def clean_item(
    item: CleanInput,
    *,
    anchor_ts: str | None = None,
    window_secs: float = DEFAULT_STALENESS_WINDOW_SECONDS,
) -> CleanResult:
    """Run the structural gates then relevance trimming on one candidate (R17).

    Order matters: structural checks (reachability, staleness) come BEFORE any
    relevance work, and all of it comes before the LLM ever sees the text.
    """
    if not is_reachable(item):
        return CleanResult(
            repo=item.repo, text="", url=item.url, ts=item.ts, kept=False,
            reason="unreachable/empty content",
        )
    if is_stale(item.ts, anchor_ts, window_secs=window_secs):
        return CleanResult(
            repo=item.repo, text="", url=item.url, ts=item.ts, kept=False,
            reason="stale content (older than freshness window)",
        )
    trimmed = trim_relevant_paragraphs(item.text, item.relevance_terms)
    if not trimmed.strip():
        return CleanResult(
            repo=item.repo, text="", url=item.url, ts=item.ts, kept=False,
            reason="no relevant content after trimming",
        )
    return CleanResult(repo=item.repo, text=trimmed, url=item.url, ts=item.ts, kept=True)


def clean_batch(
    items: list[CleanInput],
    *,
    anchor_ts: str | None = None,
    window_secs: float = DEFAULT_STALENESS_WINDOW_SECONDS,
) -> list[CleanResult]:
    """Clean a batch, returning only the candidates that survived the gates.

    Dropped (stale/unreachable/empty) candidates are filtered out so they never
    reach the LLM (R17).
    """
    out: list[CleanResult] = []
    for item in items:
        res = clean_item(item, anchor_ts=anchor_ts, window_secs=window_secs)
        if res.kept:
            out.append(res)
    return out
