"""Trend collector: OSS Insight trending + star snapshot fallback + scope filter
(U12, R4/R6/R19/R23; F2).

Mode B's discovery-side collector. Two signal sources for "what is heating up":

  * **OSS Insight trending (primary)** — a daily trending list (by language). Each
    candidate carries its current ``stargazers`` count and topics/description used
    for scope filtering.
  * **Star snapshot diff (fallback)** — each run records a ``stargazers_count``
    snapshot into the state store (U2). The delta between the latest two snapshots
    yields a growth rate when OSS Insight is unreachable. On the FIRST snapshot for
    a repo there is no prior point, so no growth is emitted that day — only the
    snapshot is recorded (Edge: first-run).

Degradation (NOT silent): when OSS Insight is unreachable the collector switches
to the snapshot-diff fallback AND raises an audit warning record so the operator
knows the primary path failed (Edge: OSS Insight down → fallback + alert).

Scope filter (F2, R4): a candidate is in the AI scope when ANY configured topic
tag matches its topics OR any configured keyword appears in its name/description
(topic + keyword OR — mirrors trend_scope config). Non-AI candidates are filtered
out before they ever reach the (expensive, untrusted) explain LLM.

Security (R23):
  * SSRF allowlist — only ``ossinsight.io`` / ``api.ossinsight.io`` / ``github.com``
    / ``api.github.com`` may be contacted. A URL injected into the trending
    response that points off-allowlist is REJECTED and never fetched.
  * No redirect following — the injected client must be ``follow_redirects=False``;
    the public entry points assert this rather than trusting the caller.

All HTTP is injected via an ``httpx.Client`` so tests mock transport; no real
network. The trending JSON text is untrusted external content — names/topics are
carried as data and only ever reach an LLM through ``llm.py`` (in U13), never the
instruction slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..store.state import StateStore
from .github import SSRFError, _require_no_redirects

# SSRF allowlist for this module (R23). Only these hosts may ever be contacted.
ALLOWED_HOSTS = frozenset(
    {"ossinsight.io", "api.ossinsight.io", "github.com", "api.github.com"}
)

# OSS Insight trending endpoint (language-scoped trending repos).
_OSSINSIGHT_TRENDING_URL = "https://api.ossinsight.io/v1/trends/repos/"


class TrendCollectError(Exception):
    """Raised on a trend-collection contract violation (e.g. SSRF)."""


@dataclass
class TrendCandidate:
    """One trending repo candidate (untrusted external content).

    ``stargazers`` is the current star count (for the snapshot diff). ``topics`` /
    ``description`` / ``name`` feed the scope filter and, downstream, the explain
    LLM — they are DATA, never instructions.
    """

    repo: str
    stargazers: int = 0
    topics: list[str] = field(default_factory=list)
    description: str = ""
    url: str = ""
    # Growth rate (stars/day) attached when a prior snapshot exists; None on first
    # observation (Edge: first run → record snapshot, no growth that day).
    growth_per_day: float | None = None
    # Where the growth number came from: "ossinsight" or "snapshot-diff".
    growth_source: str = ""


@dataclass
class TrendCollectResult:
    """Outcome of a trend-collection run.

    ``candidates`` are scope-filtered AI-range repos. ``degraded`` is True when the
    primary OSS Insight path failed and the snapshot-diff fallback was used.
    ``warnings`` carries the non-silent degradation / SSRF-rejection audit lines.
    """

    candidates: list[TrendCandidate] = field(default_factory=list)
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SSRF gate (R23)
# ---------------------------------------------------------------------------
def _assert_allowed(url: str) -> None:
    host = httpx.URL(url).host
    if host not in ALLOWED_HOSTS:
        raise SSRFError(f"refusing request to non-allowlisted host {host!r} (R23)")


def assert_candidate_url_allowed(url: str) -> None:
    """Validate a trending-response-embedded URL against the allowlist (R23).

    Trending payloads can embed arbitrary ``html_url``/links; before any such URL
    could be acted on it must be on the allowlist. Off-allowlist URLs are rejected,
    never fetched.
    """
    _assert_allowed(url)


def _safe_candidate_url(url: str) -> tuple[str, bool]:
    """Return ``(url, ok)``: keep an on-allowlist URL, drop an off-allowlist one.

    An attacker-injected off-allowlist ``url`` in the trending response must never
    become a stored, actionable link nor be fetched. We validate (not fetch) and
    drop on rejection; ``ok=False`` lets the caller record a non-silent warning.
    """
    if not url:
        return "", True
    try:
        assert_candidate_url_allowed(url)
    except SSRFError:
        return "", False
    return url, True


# ---------------------------------------------------------------------------
# Scope filter (F2, R4) — topic + keyword OR
# ---------------------------------------------------------------------------
def in_scope(candidate: TrendCandidate, *, topics: list[str], keywords: list[str]) -> bool:
    """True when the candidate is in the configured AI scope (topic OR keyword).

    Topic match is exact (case-insensitive) against the candidate's topic tags;
    keyword match is a case-insensitive substring of name/description. The two
    combine as OR (mirrors trend_scope config). Empty scope matches nothing.
    """
    topic_set = {t.lower() for t in topics if t}
    cand_topics = {t.lower() for t in candidate.topics if t}
    if topic_set & cand_topics:
        return True
    haystack = f"{candidate.repo} {candidate.description}".lower()
    return any(k.lower() in haystack for k in keywords if k)


def filter_scope(
    candidates: list[TrendCandidate], *, topics: list[str], keywords: list[str]
) -> list[TrendCandidate]:
    """Keep only candidates in the AI scope (F2). Non-AI candidates are dropped."""
    return [c for c in candidates if in_scope(c, topics=topics, keywords=keywords)]


# ---------------------------------------------------------------------------
# OSS Insight trending (primary)
# ---------------------------------------------------------------------------
def fetch_ossinsight_trending(
    client: httpx.Client,
    *,
    language: str | None = None,
    period: str = "past_24_hours",
) -> tuple[list[TrendCandidate], list[str]]:
    """Fetch the OSS Insight trending list (primary path).

    Returns ``(candidates, warnings)``. Raises :class:`TrendCollectError` wrapping
    an :class:`SSRFError` on a contract violation (off-allowlist host / redirects).
    On an unreachable/non-200 OSS Insight, raises so the caller switches to the
    snapshot-diff fallback (degradation is handled by :func:`collect_trends`, not
    silently here).

    Security: host allowlist + no redirects (R23). An off-allowlist URL embedded in
    the response is dropped (not fetched) and recorded as a warning.
    """
    _assert_allowed(_OSSINSIGHT_TRENDING_URL)
    _require_no_redirects(client)
    params: dict[str, str] = {"period": period}
    if language:
        params["language"] = language
    resp = client.get(
        _OSSINSIGHT_TRENDING_URL, params=params, headers={"User-Agent": "transmutary"}
    )
    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"OSS Insight trending returned {resp.status_code}",
            request=resp.request,
            response=resp,
        )
    payload = resp.json()
    return _parse_ossinsight(payload)


def _parse_ossinsight(payload: dict) -> tuple[list[TrendCandidate], list[str]]:
    """Parse the OSS Insight trending payload into candidates.

    OSS Insight returns ``{"data": {"rows": [...]}}`` (or a bare list). Each row
    carries at least a ``repo_name`` and ``stars``. Rows are UNTRUSTED data; any
    embedded ``html_url`` is validated against the allowlist before being stored.
    """
    warnings: list[str] = []
    rows = _rows_of(payload)
    candidates: list[TrendCandidate] = []
    for row in rows:
        repo = str(row.get("repo_name") or row.get("repo") or row.get("full_name") or "").strip()
        if not repo:
            continue
        stars = _to_int(row.get("stars") or row.get("stargazers") or row.get("stargazers_count"))
        topics = _to_str_list(row.get("topics") or row.get("collection_names"))
        description = str(row.get("description") or "")
        raw_url = str(row.get("html_url") or row.get("url") or "")
        safe_url, ok = _safe_candidate_url(raw_url)
        if not ok:
            warnings.append(
                f"trend candidate {repo!r} carried an off-allowlist URL "
                f"{raw_url!r}; dropped, not fetched (R23)"
            )
        # OSS Insight may directly provide a period growth metric. ONLY an explicit
        # increment field counts — the total ``stars`` is the snapshot value, not a
        # growth rate, so it must not be mistaken for one (it backfills via the
        # snapshot diff instead).
        period_stars = row.get("stars_increment")
        growth = _to_float(period_stars) if period_stars is not None else None
        candidates.append(
            TrendCandidate(
                repo=repo,
                stargazers=stars,
                topics=topics,
                description=description,
                url=safe_url,
                growth_per_day=growth,
                growth_source="ossinsight" if growth is not None else "",
            )
        )
    return candidates, warnings


def _rows_of(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            rows = data.get("rows")
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        rows = payload.get("rows")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_str_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if isinstance(v, str) and v:
        return [s.strip() for s in v.split(",") if s.strip()]
    return []


# ---------------------------------------------------------------------------
# Star snapshot diff (fallback)
# ---------------------------------------------------------------------------
def snapshot_growth(
    store: StateStore,
    repo: str,
    stargazers: int,
    *,
    ts: float,
) -> float | None:
    """Record a star snapshot and return the growth rate (stars/day) vs the prior.

    On the FIRST snapshot for ``repo`` there is no prior point, so we record the
    snapshot and return ``None`` (Edge: first run → no growth that day). On a later
    run the delta against the most recent prior snapshot, normalized to per-day, is
    returned. A non-positive time delta yields ``None`` (no spurious rate).
    """
    prior = store.get_star_snapshots(repo)
    store.add_star_snapshot(repo, stargazers, ts)
    if not prior:
        return None
    last = prior[-1]
    dt_secs = ts - last.ts
    if dt_secs <= 0:
        return None
    delta_stars = stargazers - last.stargazers
    return delta_stars / (dt_secs / 86400.0)


# ---------------------------------------------------------------------------
# Public collection entry point
# ---------------------------------------------------------------------------
def collect_trends(
    client: httpx.Client,
    store: StateStore,
    *,
    topics: list[str],
    keywords: list[str],
    language: str | None = None,
    period: str = "past_24_hours",
    snapshot_candidates: list[TrendCandidate] | None = None,
    ts: float,
) -> TrendCollectResult:
    """Collect trending AI-range candidates (OSS Insight primary, snapshot fallback).

    Flow:
      1. Try OSS Insight trending (primary). Each candidate's current star count is
         snapshotted into state, and a snapshot-diff growth is attached when no
         OSS Insight period metric was present.
      2. If OSS Insight is unreachable/non-200, switch to the snapshot-diff fallback
         over ``snapshot_candidates`` (the caller supplies current star counts, e.g.
         from per-repo GitHub snapshots) AND record a non-silent degradation
         warning (Edge: OSS Insight down → fallback + alert).
      3. Scope-filter to the AI range (F2). Non-AI candidates are dropped.

    Returns a :class:`TrendCollectResult` (filtered candidates + degraded flag +
    audit warnings). Security: host allowlist + no redirects (R23).
    """
    _require_no_redirects(client)
    warnings: list[str] = []
    degraded = False

    try:
        candidates, fetch_warnings = fetch_ossinsight_trending(
            client, language=language, period=period
        )
        warnings.extend(fetch_warnings)
        # Snapshot each candidate; backfill growth from the snapshot diff when OSS
        # Insight did not directly provide a period growth metric.
        for cand in candidates:
            growth = snapshot_growth(store, cand.repo, cand.stargazers, ts=ts)
            if cand.growth_per_day is None:
                cand.growth_per_day = growth
                cand.growth_source = "snapshot-diff" if growth is not None else ""
    except SSRFError:
        # A contract violation is NOT a degradation — surface it.
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        # OSS Insight unreachable / bad payload → snapshot-diff fallback + alert.
        degraded = True
        warnings.append(
            f"OSS Insight trending unreachable ({type(exc).__name__}: {exc}); "
            "switched to star-snapshot diff fallback (NOT silent) (R6)"
        )
        candidates = []
        for cand in snapshot_candidates or []:
            growth = snapshot_growth(store, cand.repo, cand.stargazers, ts=ts)
            cand.growth_per_day = growth
            cand.growth_source = "snapshot-diff" if growth is not None else ""
            candidates.append(cand)

    filtered = filter_scope(candidates, topics=topics, keywords=keywords)
    return TrendCollectResult(candidates=filtered, degraded=degraded, warnings=warnings)
