"""Dashboard view-data read layer (U2, R-D1–R-D6/R-D9/R-D15).

The ONLY place the dashboard touches the stores. It composes the ``StateStore``
and ``ArtifactStore`` read interfaces into plain, display-only view models that
the templates consume. Two invariants live here:

  * **No credentials / tokens (R-D9).** This layer never returns subscriber token
    values or hashes, and never reads env credentials. The view models carry no
    store handle and no credential fields — only what a page renders.
  * **URL scheme allow-list (R-D15).** ``autoescape`` neutralises HTML text
    context but NOT a ``javascript:`` URL placed in an ``href``. So every external
    URL that could become an ``href`` (report ``Sources``) is passed through
    :func:`_safe_url`, which blanks anything that is not ``http``/``https``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import Settings
from ..service import effective_repos
from ..store.artifacts import ArtifactStore
from ..store.state import StateStore

# Schemes allowed to survive into an href. Everything else (javascript:, data:,
# vbscript:, …) is blanked so autoescape's text-context guard is not bypassed.

# Severities that route to the urgent / supply-chain bucket on the overview.
# Mirrors Severity.is_urgent (critical + high → immediate route).
_ALERT_SEVERITIES = frozenset({"critical", "high"})

# Parse one rendered source line: ``- `id` url (fetched ts)`` (see
# ArtifactStore._render_markdown). Tolerant: a line that does not match is skipped
# (e.g. the "待核实信号" no-sources marker), so sources are best-effort.
_SOURCE_LINE = re.compile(r"^- `(?P<id>[^`]*)` (?P<url>\S+) \(fetched (?P<fetched>.*)\)$")


def _safe_url(url: str | None) -> str | None:
    """Return ``url`` only if it is an http(s) URL, else None (R-D15).

    A strict prefix allow-list — anything that is not ``http://`` / ``https://``
    (javascript:, data:, vbscript:, scheme-relative, …) is blanked so it can never
    land in an href.
    """
    if not url:
        return None
    low = url.strip().lower()
    if low.startswith("http://") or low.startswith("https://"):
        return url
    return None


# --- view models -------------------------------------------------------------


@dataclass(frozen=True)
class WatchEntry:
    repo: str
    source: str  # "config" | promoted source (e.g. "mode-b" / "manual")


@dataclass(frozen=True)
class ReportCard:
    repo: str
    kind: str  # "diagnose" | "explain"
    severity: str
    title: str
    ts: int
    filename: str


@dataclass(frozen=True)
class SourceLink:
    source_id: str
    url: str | None  # already passed through _safe_url; None = not linkable
    fetched_at: str


@dataclass(frozen=True)
class ReportView:
    card: ReportCard
    body: str  # raw markdown, rendered as escaped <pre> by the template (KTD-Dash-3)
    sources: tuple[SourceLink, ...]


@dataclass(frozen=True)
class RepoRuntime:
    repo: str
    in_watchlist: bool
    source: str
    baseline_rate: float | None
    latest_stars: int | None
    star_growth: int | None  # latest - earliest snapshot, when ≥2 snapshots
    cursor: str | None


@dataclass(frozen=True)
class FeedLink:
    route: str
    href: str  # local relative /feed/<route> — never carries a token (R-D9/R-D20)


@dataclass(frozen=True)
class Overview:
    watchlist: tuple[WatchEntry, ...]
    recent_reports: tuple[ReportCard, ...]
    supply_chain_alerts: tuple[ReportCard, ...]
    trend_candidates: tuple[ReportCard, ...]
    feeds: tuple[FeedLink, ...]


# --- parsing helpers ---------------------------------------------------------


def _parse_meta(body: str) -> tuple[str, str]:
    """Extract (title, severity) from a rendered report markdown header."""
    title = ""
    severity = "info"
    for line in body.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("- severity:"):
            severity = line.split(":", 1)[1].strip()
        elif line.startswith("## "):
            break  # header ends at the first section
    return title or "(untitled)", severity


def _parse_sources(body: str) -> tuple[SourceLink, ...]:
    """Parse the ``## Sources`` block into sanitised SourceLinks (R-D15)."""
    out: list[SourceLink] = []
    in_sources = False
    for line in body.splitlines():
        if line.startswith("## Sources"):
            in_sources = True
            continue
        if not in_sources:
            continue
        m = _SOURCE_LINE.match(line.strip())
        if m:
            out.append(
                SourceLink(
                    source_id=m.group("id"),
                    url=_safe_url(m.group("url")),
                    fetched_at=m.group("fetched"),
                )
            )
    return tuple(out)


def _card(repo: str, ref, body: str) -> ReportCard:
    title, severity = _parse_meta(body)
    return ReportCard(
        repo=repo,
        kind=ref.kind,
        severity=severity,
        title=title,
        ts=ref.ts,
        filename=ref.filename,
    )


def _all_cards(artifacts: ArtifactStore) -> list[ReportCard]:
    cards: list[ReportCard] = []
    for repo in artifacts.list_repos():
        for ref in artifacts.list_reports(repo):
            body = artifacts.read_report(repo, ref.filename)
            if body is None:
                continue
            cards.append(_card(repo, ref, body))
    cards.sort(key=lambda c: c.ts, reverse=True)
    return cards


# --- builders ----------------------------------------------------------------


def build_watchlist(settings: Settings, store: StateStore) -> tuple[WatchEntry, ...]:
    """Effective watchlist (config ∪ promoted), each entry tagged with its source.

    Reuses :func:`service.effective_repos` as the single source of truth for the
    repo set, then tags each: config-watchlist repos are ``config``; the rest are
    promoted and carry their stored promotion source.
    """
    config_repos = set(settings.watchlist.repo_names())
    promoted_source = {
        row["repo"]: row["source"] for row in store.list_promoted_meta()
    }
    entries = []
    for repo in effective_repos(settings, store):
        if repo in config_repos:
            entries.append(WatchEntry(repo=repo, source="config"))
        else:
            entries.append(WatchEntry(repo=repo, source=promoted_source.get(repo, "promoted")))
    return tuple(entries)


def build_overview(
    settings: Settings,
    store: StateStore,
    artifacts: ArtifactStore,
    *,
    limit: int = 20,
) -> Overview:
    """Assemble the overview page view model (R-D1–R-D6)."""
    watchlist = build_watchlist(settings, store)
    cards = _all_cards(artifacts)
    alerts = tuple(c for c in cards if c.severity in _ALERT_SEVERITIES)
    trends = tuple(c for c in cards if c.kind == "explain")
    feeds = (
        FeedLink(route="immediate", href="/feed/immediate"),
        FeedLink(route="digest", href="/feed/digest"),
    )
    return Overview(
        watchlist=watchlist,
        recent_reports=tuple(cards[:limit]),
        supply_chain_alerts=alerts[:limit],
        trend_candidates=trends[:limit],
        feeds=feeds,
    )


def build_repo_runtime(
    settings: Settings,
    store: StateStore,
    artifacts: ArtifactStore,
    repo: str,
) -> tuple[RepoRuntime, tuple[ReportCard, ...]] | None:
    """Per-repo runtime view + that repo's reports. None if repo is not observed.

    "Observed" = in the effective watchlist OR has archived reports — so a demoted
    repo with history is still viewable, but an arbitrary unknown repo is not (no
    enumeration oracle beyond what is already archived).
    """
    watchlist = {e.repo: e.source for e in build_watchlist(settings, store)}
    refs = artifacts.list_reports(repo)
    if repo not in watchlist and not refs:
        return None

    snapshots = store.get_star_snapshots(repo)
    latest_stars = snapshots[-1].stargazers if snapshots else None
    star_growth = (
        snapshots[-1].stargazers - snapshots[0].stargazers
        if len(snapshots) >= 2
        else None
    )
    baseline = store.get_issue_baseline(repo)
    runtime = RepoRuntime(
        repo=repo,
        in_watchlist=repo in watchlist,
        source=watchlist.get(repo, "archived"),
        baseline_rate=baseline["rate"] if baseline else None,
        latest_stars=latest_stars,
        star_growth=star_growth,
        cursor=store.get_cursor(repo),
    )
    cards = tuple(
        _card(repo, ref, body)
        for ref in refs
        if (body := artifacts.read_report(repo, ref.filename)) is not None
    )
    return runtime, cards


def build_report_view(
    artifacts: ArtifactStore, repo: str, filename: str
) -> ReportView | None:
    """Single report view: raw body + sanitised sources. None if not found."""
    body = artifacts.read_report(repo, filename)
    if body is None:
        return None
    title, severity = _parse_meta(body)
    # kind is the trailing token of the filename: <ts>-<kind>.md
    kind = filename.rsplit("-", 1)[-1][:-3]
    ts = int(filename.split("-", 1)[0])
    card = ReportCard(
        repo=repo, kind=kind, severity=severity, title=title, ts=ts, filename=filename
    )
    return ReportView(card=card, body=body, sources=_parse_sources(body))
