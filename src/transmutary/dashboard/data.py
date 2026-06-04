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
from ..deliver.routes import DeliveryRoute
from ..effective_config import (
    effective_delivery,
    effective_repo_sources,
    effective_trend_scope,
)
from ..report.render import render_markdown
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
    body: str  # raw markdown (untrusted) — kept for the JSON contract
    body_zh: str | None  # Chinese markdown (untrusted), None when unavailable
    sources: tuple[SourceLink, ...]
    body_html: str = ""  # rendered HTML (R2) — WebUI template only, NOT in to_dict
    body_zh_html: str = ""  # rendered Chinese HTML — WebUI template only

    def to_dict(self) -> dict:
        # `body` / `body_zh` are untrusted external markdown — a JSON consumer MUST
        # escape them before any HTML rendering. The trust marker documents that contract.
        # body_html / body_zh_html are deliberately NOT exposed here: pre-rendered HTML
        # over the JSON API would invite consumers to emit it without their own checks.
        d: dict = {
            "card": self.card.to_dict(),
            "body": self.body,
            "body_zh": self.body_zh,
            "sources": [s.to_dict() for s in self.sources],
            "_content_trust": "external",
        }
        return d


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


@dataclass(frozen=True)
class SecretStatus:
    name: str
    configured: bool

    def to_dict(self) -> dict:
        return {"name": self.name, "configured": self.configured}


@dataclass(frozen=True)
class AdminSettingsView:
    repos: tuple[WatchEntry, ...]
    admin_dependency_edges: tuple[tuple[str, str], ...]
    trend_topics: tuple[str, ...]
    trend_keywords: tuple[str, ...]
    email_recipients: tuple[str, ...]
    digest_hour: int
    smtp_host: str | None
    state_db_path: str
    artifact_root: str
    secrets: tuple[SecretStatus, ...]

    def to_dict(self) -> dict:
        return {
            "repos": [r.to_dict() for r in self.repos],
            "admin_dependency_edges": [
                {"from_repo": a, "to_repo": b} for a, b in self.admin_dependency_edges
            ],
            "trend_topics": list(self.trend_topics),
            "trend_keywords": list(self.trend_keywords),
            "email_recipients": list(self.email_recipients),
            "digest_hour": self.digest_hour,
            "smtp_host": self.smtp_host,
            "state_db_path": self.state_db_path,
            "artifact_root": self.artifact_root,
            "secrets": [s.to_dict() for s in self.secrets],
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

    Reuses :func:`watchlist.effective_repos` as the single source of truth for the
    repo set, then tags each: config-watchlist repos are ``config``; the rest are
    promoted and carry their stored promotion source.
    """
    sources = effective_repo_sources(settings, store)
    promoted_source = {row["repo"]: row["source"] for row in store.list_promoted_meta()}
    entries = []
    for repo in sources:
        source = sources[repo]
        if source == "config":
            entries.append(WatchEntry(repo=repo, source="config", demotable=False))
        elif source == "admin":
            entries.append(WatchEntry(repo=repo, source="admin", demotable=True))
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
        FeedLink(
            route=DeliveryRoute.IMMEDIATE.value,
            href=f"/feed/{DeliveryRoute.IMMEDIATE.value}",
        ),
        FeedLink(route=DeliveryRoute.DIGEST.value, href=f"/feed/{DeliveryRoute.DIGEST.value}"),
    )
    return Overview(
        watchlist=watchlist,
        recent_reports=tuple(cards[:limit]),
        supply_chain_alerts=alerts[:limit],
        trend_candidates=trends[:limit],
        feeds=feeds,
        promotable_repos=frozenset(c.repo for c in trends[:limit] if c.repo not in effective),
    )


def build_admin_settings(
    settings: Settings,
    store: StateStore,
    *,
    secret_env: dict[str, str | None],
) -> AdminSettingsView:
    delivery = effective_delivery(settings, store)
    trend_scope = effective_trend_scope(settings, store)
    edges = tuple(
        (edge.from_repo, edge.to_repo) for edge in store.list_admin_dependency_edges()
    )
    secrets = tuple(
        SecretStatus(name=name, configured=bool(value))
        for name, value in sorted(secret_env.items())
    )
    return AdminSettingsView(
        repos=build_watchlist(settings, store),
        admin_dependency_edges=edges,
        trend_topics=tuple(trend_scope.topics),
        trend_keywords=tuple(trend_scope.keywords),
        email_recipients=tuple(delivery.email_recipients),
        digest_hour=delivery.digest_hour,
        smtp_host=delivery.smtp_host,
        state_db_path=delivery.state_db_path,
        artifact_root=delivery.artifact_root,
        secrets=secrets,
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

    The English body comes from the sidecar ``body_md`` (English-only); the Chinese
    body from ``body_md_zh`` (ADV-10). Both are UNTRUSTED markdown and are rendered
    to safe HTML via :func:`render_markdown`. Card fields and sources come from the
    trusted sidecar JSON (R-D15). The bilingual ``.md`` file on disk concatenates both
    languages for human reading; the WebUI must NOT use it for the English tab (it
    would leak Chinese into the English view).
    """
    meta = artifacts.read_meta(repo, filename)
    if meta is None:
        return None
    # English body: sidecar body_md (English-only). Fall back to the .md file for
    # pre-bilingual reports that predate the body_md sidecar field.
    body = meta.get("body_md")
    if body is None:
        body = artifacts.read_report(repo, filename)
    if body is None:
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
    # Read bilingual body from sidecar (ADV-10), not from .md parsing.
    body_zh = meta.get("body_md_zh")
    return ReportView(
        card=card,
        body=body,
        body_zh=body_zh,
        sources=_sources_from_meta(meta),
        body_html=render_markdown(body),
        body_zh_html=render_markdown(body_zh),
    )
