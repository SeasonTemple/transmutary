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

from dataclasses import dataclass

from ..config import Settings
from ..service import effective_repos
from ..store.artifacts import ArtifactStore
from ..store.state import StateStore

# Severities that route to the urgent / supply-chain bucket on the overview.
# Mirrors Severity.is_urgent (critical + high → immediate route).
_ALERT_SEVERITIES = frozenset({"critical", "high"})

# Severity values that may reach a CSS class / alert bucket. Anything outside this
# closed set (e.g. a corrupt sidecar) is coerced to "info" so no arbitrary string
# flows into the template (defence-in-depth even though autoescape covers it).
_KNOWN_SEVERITIES = frozenset({"critical", "high", "normal", "info"})


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
    demotable: bool

    def to_dict(self) -> dict:
        # Explicit allow-list (NOT dataclasses.asdict) so a future field added to
        # this dataclass can never silently leak into the JSON surface (R-S2).
        return {"repo": self.repo, "source": self.source, "demotable": self.demotable}


@dataclass(frozen=True)
class ReportCard:
    repo: str
    kind: str  # "diagnose" | "explain"
    severity: str
    title: str
    ts: int
    filename: str

    def to_dict(self) -> dict:
        # Explicit allow-list (R-S2). `title` is external-origin → trust marker.
        return {
            "repo": self.repo,
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "ts": self.ts,
            "filename": self.filename,
            "_content_trust": "external",  # title comes from upstream repos
        }


@dataclass(frozen=True)
class SourceLink:
    source_id: str
    url: str | None  # already passed through _safe_url; None = not linkable
    fetched_at: str

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "fetched_at": self.fetched_at,
        }


@dataclass(frozen=True)
class ReportView:
    card: ReportCard
    body: str  # raw markdown, rendered as escaped <pre> by the template (KTD-Dash-3)
    sources: tuple[SourceLink, ...]

    def to_dict(self) -> dict:
        # `body` is untrusted external markdown — a JSON consumer MUST escape it
        # before any HTML rendering. The trust marker documents that contract.
        return {
            "card": self.card.to_dict(),
            "body": self.body,
            "sources": [s.to_dict() for s in self.sources],
            "_content_trust": "external",
        }


@dataclass(frozen=True)
class RepoRuntime:
    repo: str
    in_watchlist: bool
    source: str
    baseline_rate: float | None
    latest_stars: int | None
    star_growth: int | None  # latest - earliest snapshot, when ≥2 snapshots
    cursor: str | None
    promotable: bool
    demotable: bool

    def to_dict(self) -> dict:
        # Explicit allow-list (NOT dataclasses.asdict) so a future field can never
        # silently leak into the JSON surface (R-S2).
        return {
            "repo": self.repo,
            "in_watchlist": self.in_watchlist,
            "source": self.source,
            "baseline_rate": self.baseline_rate,
            "latest_stars": self.latest_stars,
            "star_growth": self.star_growth,
            "cursor": self.cursor,
            "promotable": self.promotable,
            "demotable": self.demotable,
        }


@dataclass(frozen=True)
class FeedLink:
    route: str
    href: str

    def to_dict(self) -> dict:
        # href is a local relative /feed/<route> — never carries a token (R-D9).
        return {"route": self.route, "href": self.href}


@dataclass(frozen=True)
class Overview:
    watchlist: tuple[WatchEntry, ...]
    recent_reports: tuple[ReportCard, ...]
    supply_chain_alerts: tuple[ReportCard, ...]
    trend_candidates: tuple[ReportCard, ...]
    feeds: tuple[FeedLink, ...]
    promotable_repos: frozenset[str]

    def to_dict(self) -> dict:
        return {
            "watchlist": [w.to_dict() for w in self.watchlist],
            "recent_reports": [c.to_dict() for c in self.recent_reports],
            "supply_chain_alerts": [c.to_dict() for c in self.supply_chain_alerts],
            "trend_candidates": [c.to_dict() for c in self.trend_candidates],
            "feeds": [f.to_dict() for f in self.feeds],
            "promotable_repos": sorted(self.promotable_repos),
        }


# --- trusted-metadata helpers (read the sidecar JSON, never parse the body) --


def _coerce_severity(value: object) -> str:
    """Constrain a severity to the known closed set (else 'info')."""
    return value if value in _KNOWN_SEVERITIES else "info"


def _sources_from_meta(meta: dict) -> tuple[SourceLink, ...]:
    """Build sanitised SourceLinks from the trusted sidecar (R-D15)."""
    out: list[SourceLink] = []
    for s in meta.get("sources", []) or []:
        if not isinstance(s, dict):
            continue
        out.append(
            SourceLink(
                source_id=str(s.get("source_id", "")),
                url=_safe_url(s.get("url")),
                fetched_at=str(s.get("fetched_at", "")),
            )
        )
    return tuple(out)


def _card_from_meta(repo: str, ref, meta: dict) -> ReportCard:
    return ReportCard(
        repo=repo,
        kind=str(meta.get("kind", ref.kind)),
        severity=_coerce_severity(meta.get("severity")),
        title=str(meta.get("title", "(untitled)")),
        ts=ref.ts,
        filename=ref.filename,
    )


def _all_cards(artifacts: ArtifactStore) -> list[ReportCard]:
    cards: list[ReportCard] = []
    for repo in artifacts.list_repos():
        for ref in artifacts.list_reports(repo):
            meta = artifacts.read_meta(repo, ref.filename)
            if meta is None:
                continue
            cards.append(_card_from_meta(repo, ref, meta))
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
            entries.append(WatchEntry(repo=repo, source="config", demotable=False))
        else:
            entries.append(
                WatchEntry(
                    repo=repo,
                    source=promoted_source.get(repo, "promoted"),
                    demotable=True,
                )
            )
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
    effective = {entry.repo for entry in watchlist}
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
        promotable_repos=frozenset(c.repo for c in trends[:limit] if c.repo not in effective),
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
        promotable=repo not in watchlist,
        demotable=watchlist.get(repo) not in (None, "config", "archived"),
    )
    cards = tuple(
        _card_from_meta(repo, ref, meta)
        for ref in refs
        if (meta := artifacts.read_meta(repo, ref.filename)) is not None
    )
    return runtime, cards


def build_report_view(
    artifacts: ArtifactStore, repo: str, filename: str
) -> ReportView | None:
    """Single report view: raw body + trusted card/sources. None if not found.

    The body is the raw markdown (rendered as escaped ``<pre>``); the card fields
    and sources come from the trusted sidecar JSON (R-D15), never re-parsed from
    the untrusted body.
    """
    body = artifacts.read_report(repo, filename)
    if body is None:
        return None
    meta = artifacts.read_meta(repo, filename)
    if meta is None:
        return None
    ts = int(filename.split("-", 1)[0])
    card = ReportCard(
        repo=repo,
        kind=str(meta.get("kind", "")),
        severity=_coerce_severity(meta.get("severity")),
        title=str(meta.get("title", "(untitled)")),
        ts=ts,
        filename=filename,
    )
    return ReportView(card=card, body=body, sources=_sources_from_meta(meta))
