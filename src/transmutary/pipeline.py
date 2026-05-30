"""Pipeline orchestration: compose the ready units into three mode-tick pipelines
(U2-U5; KTD-A/KTD-A2/KTD-D/KTD-E/KTD-F).

This is the keystone wiring layer the Phase 0/1/2 units were built for. It does
NOT reimplement any unit — every tick is pure COMPOSITION of the existing
collect / dedup / filter / diagnose / explain / deliver units, injecting the
shared ``store`` / ``client`` / ``call_fn`` seams so the whole chain stays
mockable and the Phase 1/2 invariants (LLM only via ``llm.py``, injection
data-slotting, SSRF allowlist, deterministic OSV/GHSA cross-check) are preserved
end to end.

Three ticks, deliberately kept as INDEPENDENT functions (KTD-A2 — no Pipeline
base class / template method, same posture as R14's no-channel-abstraction):
their dedup strategy, report arity and routing differ in kind, so a premature
``Pipeline.collect/dedup/report/deliver`` template would leak wrong assumptions.

  * :func:`run_release_issue_tick` — mode A event pipeline (release dedup +
    issue-surge filter → diagnose → deliver).
  * :func:`run_security_tick` — mode A supply-chain pipeline (resolve deps →
    supply-chain advisories → dedup → immediate high-risk alert).
  * :func:`run_trend_tick` — mode B trend pipeline (collect trends → artifact
    diff (inside explain) → batch summarize → digest deliver).

Credentials never enter ``repr`` / logs / SQLite (KTD-D/KTD4): the runtime holds
an :class:`OutboundDelivery` (whose secret-bearing fields the dataclass keeps out
of structured logs) and reads the GitHub/LLM secrets straight from
:class:`~transmutary.config.Credentials` accessors at call time.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx

from . import llm
from .clean import CleanInput
from .collect.deps import resolve_repo_dependencies
from .collect.github import collect_repo, make_client
from .collect.security import build_alert, collect_supply_chain
from .collect.trend import TrendCandidate, collect_trends
from .config import Credentials, Settings
from .dedup import SourceItem, dedup_advisory, dedup_release
from .deliver.stub import OutboundDelivery, deliver
from .filter import ConservativeReview, IssueObservation, filter_issue_surge
from .report.diagnose import EventContext, diagnose
from .report.explain import explain_trends
from .report.schema import Severity
from .store.artifacts import ArtifactStore
from .store.state import StateStore

logger = logging.getLogger("transmutary.pipeline")


# ---------------------------------------------------------------------------
# U2 — shared runtime bootstrap
# ---------------------------------------------------------------------------
@dataclass
class PipelineRuntime:
    """Shared infrastructure the three ticks reuse (KTD-A/KTD-E).

    A single :class:`StateStore` is shared across all ticks (single serialized
    writer + WAL reads, KTD-E), one ``httpx.Client`` (SSRF-safe, redirects off),
    and one :class:`OutboundDelivery` config (RSS always; email leg only when
    configured, KTD-D). ``settings``/``creds`` are held so each tick can pull the
    watchlist, trend scope, LLM credentials and base_url at call time.

    The since-cursor for incremental issue collection is PERSISTED in the state
    store's ``collect_cursor`` table (U3): the cursor survives a process restart so
    an issue surge already reported in a prior process is not re-collected and
    re-diagnosed after a restart. :attr:`since_cursors` is kept as an in-process
    mirror of the persisted value (an audit/observability convenience — the store
    is the source of truth). ``coalesce``/``max_instances=1`` (KTD-F) keep a single
    writer per job, so the read-modify-write of the cursor is never raced.
    """

    store: StateStore
    client: httpx.Client
    outbound: OutboundDelivery
    settings: Settings
    artifacts: ArtifactStore
    creds: Credentials | None = None
    since_cursors: dict[str, str | None] = field(default_factory=dict)

    @property
    def has_email_leg(self) -> bool:
        """True when the email leg is configured (recipients + smtp host, KTD-D)."""
        return bool(self.outbound.email_recipients and self.outbound.smtp_host)

    @property
    def artifact_root(self) -> str:
        return self.settings.delivery.artifact_root

    def __repr__(self) -> str:  # noqa: D105 - keep secrets out of repr (KTD-D)
        return (
            "PipelineRuntime(store=<StateStore>, client=<httpx.Client>, "
            f"email_leg={'on' if self.has_email_leg else 'off'})"
        )

    __str__ = __repr__


def build_runtime(
    settings: Settings,
    creds: Credentials | None = None,
    *,
    store: StateStore | None = None,
    client: httpx.Client | None = None,
    artifacts: ArtifactStore | None = None,
) -> PipelineRuntime:
    """Construct the shared :class:`PipelineRuntime` from config + credentials (U2).

    ``store`` / ``client`` are injectable test seams; by default a real
    :class:`StateStore` (at ``delivery.state_db_path``) and the SSRF-safe
    ``make_client()`` (redirects OFF, R23) are built.

    The :class:`OutboundDelivery` is assembled from ``settings.delivery`` (its
    ``feed_dir`` defaults to ``<artifact_root>/_feed`` when unset) plus the SMTP
    credential VALUES read from ``creds``. The email leg's ``email_recipients``
    is filled from config, but downstream the email send only fires when BOTH
    recipients AND smtp_host are present (KTD-D); with neither, the deployment is
    RSS-only.
    """
    delivery = settings.delivery
    store = store if store is not None else StateStore(delivery.state_db_path)
    client = client if client is not None else make_client()
    artifacts = artifacts if artifacts is not None else ArtifactStore(delivery.artifact_root)

    feed_dir = delivery.feed_dir or os.path.join(delivery.artifact_root, "_feed")

    # The email leg is only meaningfully active when recipients + host are set.
    email_configured = bool(delivery.email_recipients and delivery.smtp_host)
    outbound = OutboundDelivery(
        feed_dir=feed_dir,
        email_recipients=list(delivery.email_recipients) if email_configured else [],
        smtp_host=delivery.smtp_host,
        smtp_user=(creds.smtp_user if creds is not None else None),
        smtp_password=(creds.smtp_password if creds is not None else None),
    )
    return PipelineRuntime(
        store=store,
        client=client,
        outbound=outbound,
        settings=settings,
        artifacts=artifacts,
        creds=creds,
    )


# ---------------------------------------------------------------------------
# Internal helpers shared by the ticks (infrastructure only, NOT a base class)
# ---------------------------------------------------------------------------
def _github_token(rt: PipelineRuntime) -> str | None:
    return rt.creds.github_token if rt.creds is not None else None


def _llm_api_key(rt: PipelineRuntime) -> str | None:
    return rt.creds.llm_api_key if rt.creds is not None else None


def _llm_base_url(rt: PipelineRuntime) -> str | None:
    return rt.settings.llm_base_url


def _embed_fn(rt: PipelineRuntime):
    """Build the L2 embedding function bound to this runtime's LLM credentials.

    Returns a ``Callable[[list[str]], list[list[float]]]`` delegating to
    :func:`llm.embed` (KTD-D, the sole embedding entry point). It is threaded into
    the issue-surge filter and the trend explainer to enable L2 semantic grouping.
    If embedding is unavailable at runtime (provider has no embedding API, KTD-F),
    ``llm.embed`` raises ``LLMError`` and the filter/explain internals degrade to
    full L3 (zero-miss, KTD-B) — the tick never crashes. The supply-chain tick does
    NOT use this: authority signals bypass L2 (KTD-C).
    """
    api_key = _llm_api_key(rt)
    base_url = _llm_base_url(rt)

    def embed_fn(texts: list[str]) -> list[list[float]]:
        return llm.embed(texts, api_key=api_key, base_url=base_url)

    return embed_fn


def _ts_to_float(iso_ts: str) -> float:
    """Best-effort ISO-8601 → epoch seconds for issue clustering (deterministic)."""
    from datetime import datetime, timezone

    if not iso_ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _deliver_report(rt: PipelineRuntime, report, urgency: Severity | None) -> None:
    """Archive then route a report (U3 per-repo artifact + inline two-branch deliver).

    The per-repo analysis artifact (`<artifact_root>/<repo>/<ts>-<kind>.md`, U3)
    is the canonical citation-bearing record (R24/KTD5) and is written FIRST, so a
    downstream delivery-channel hiccup never loses the archived diagnosis. The
    inline two-branch ``deliver`` (KTD1) then renders to `_delivered/<route>/` and
    the RSS feed (plus email on the immediate branch).
    """
    rt.artifacts.write(report)
    deliver(
        report,
        urgency,
        artifact_root=rt.artifact_root,
        outbound=rt.outbound,
    )


# ---------------------------------------------------------------------------
# U3 — mode A event pipeline (release dedup + issue surge → diagnose → deliver)
# ---------------------------------------------------------------------------
@dataclass
class ReleaseIssueTickResult:
    """Audit record of one release/issue tick (asserted by tests)."""

    repo: str
    diagnosed: int = 0  # reports produced + delivered
    releases_new: int = 0
    issue_triggered: bool = False
    needs_human_review: bool = False
    next_since: str | None = None
    notes: list[str] = field(default_factory=list)


def _related_signals(rt: PipelineRuntime, repo: str, token: str | None) -> list[CleanInput]:
    """Pull recent signals from repos reachable over declared dependency edges (F1).

    The watchlist's :class:`DependencyEdge` set maps a triggered repo to the
    associated repos whose context belongs in the diagnosis (the upstream-CLI →
    internal-gateway link). Their recent issue/release texts are collected via the
    SAME SSRF-safe collector and carried as RELATED context (untrusted data; it
    only ever reaches the model through llm.py's data slot in ``diagnose``).
    """
    edges = rt.settings.watchlist.dependency_edges
    related_repos: list[str] = []
    for edge in edges:
        if edge.from_repo == repo and edge.to_repo not in related_repos:
            related_repos.append(edge.to_repo)
        if edge.to_repo == repo and edge.from_repo not in related_repos:
            related_repos.append(edge.from_repo)

    related: list[CleanInput] = []
    for rel in related_repos:
        try:
            res = collect_repo(rt.client, rel, token=token)
        except Exception as exc:  # noqa: BLE001 - a related-repo fetch must not abort the tick
            logger.warning("related-repo collect for %r failed: %s", rel, type(exc).__name__)
            continue
        for ev in res.events:
            related.append(CleanInput(repo=rel, text=ev.text, url=ev.url, ts=ev.ts))
    return related


def run_release_issue_tick(
    rt: PipelineRuntime, repo: str, *, call_fn=llm.call
) -> ReleaseIssueTickResult:
    """Run one mode-A release/issue pipeline pass for a single watchlist repo (U3).

    Flow: ``collect_repo`` (incremental ``since`` cursor) → split release vs issue
    events. New releases (``dedup_release`` suppresses repeats across cycles, AE4)
    → ``EventContext`` → ``diagnose`` → ``deliver``. Issue events → cluster into
    ``IssueObservation`` and run ``filter_issue_surge`` against the persisted
    baseline (AE1); a triggered surge → ``EventContext`` (primary issue signals +
    dependency-edge ``related`` context, F1) → ``diagnose`` → immediate deliver.

    The issue baseline is persisted (``set_issue_baseline``) and the ``since``
    cursor advanced (never rewound). A :class:`ConservativeReview` from the judge
    (budget/LLM failure) is captured and flagged for human review — never a silent
    drop (R19).
    """
    token = _github_token(rt)
    api_key = _llm_api_key(rt)
    base_url = _llm_base_url(rt)
    result = ReleaseIssueTickResult(repo=repo)

    since = rt.store.get_cursor(repo)
    rt.since_cursors[repo] = since  # keep the in-process mirror in sync
    collected = collect_repo(rt.client, repo, token=token, since=since)

    release_events = [e for e in collected.events if e.kind in ("release", "tag")]
    issue_events = [e for e in collected.events if e.kind == "issue"]

    # --- releases: dedup across cycles (AE4), diagnose the new ones ---
    for ev in release_events:
        decision = dedup_release(rt.store, repo, ev.id, url=ev.url, text=ev.text)
        if not decision.is_new:
            continue  # same release across polls is suppressed (AE4)
        result.releases_new += 1
        ctx = EventContext(
            repo=repo,
            title=f"Release {ev.id} — {repo}",
            primary=[CleanInput(repo=repo, text=ev.text, url=ev.url, ts=ev.ts)],
            related=_related_signals(rt, repo, token),
            sources=[SourceItem(url=ev.url, text=ev.text)],
            severity=Severity.HIGH,
            anchor_ts=ev.ts,
        )
        outcome = diagnose(ctx, api_key=api_key, base_url=base_url, call_fn=call_fn)
        _deliver_report(rt, outcome.report, outcome.report.severity)
        result.diagnosed += 1

    # --- issues: surge filter against the persisted baseline (AE1) ---
    if issue_events:
        observations = [
            IssueObservation(repo=repo, text=e.text, ts=_ts_to_float(e.ts), url=e.url)
            for e in issue_events
        ]
        baseline_row = rt.store.get_issue_baseline(repo)
        baseline_rate = baseline_row["rate"] if baseline_row is not None else None
        try:
            decision = filter_issue_surge(
                observations,
                baseline_rate=baseline_rate,
                api_key=api_key,
                base_url=base_url,
                call_fn=call_fn,
                embed_fn=_embed_fn(rt),
            )
        except ConservativeReview as exc:
            # Judge unavailable / budget exhausted for a real surge — flag for
            # human review, do NOT silently drop (R19).
            result.needs_human_review = True
            result.notes.append(f"conservative-review: {exc}")
            logger.warning("issue surge for %r queued for human review: %s", repo, exc)
            decision = None

        if decision is not None and decision.triggered:
            result.issue_triggered = True
            anchor = max((e.ts for e in issue_events), default="")
            ctx = EventContext(
                repo=repo,
                title=f"Issue surge — {repo}",
                primary=[
                    CleanInput(repo=repo, text=e.text, url=e.url, ts=e.ts)
                    for e in issue_events
                ],
                related=_related_signals(rt, repo, token),
                sources=[SourceItem(url=e.url, text=e.text) for e in issue_events],
                severity=Severity.HIGH,
                anchor_ts=anchor,
            )
            outcome = diagnose(ctx, api_key=api_key, base_url=base_url, call_fn=call_fn)
            _deliver_report(rt, outcome.report, outcome.report.severity)
            result.diagnosed += 1

        # Persist/update the baseline from this window's observed rate.
        _update_issue_baseline(rt, repo, observations)

    # --- advance the since cursor (never rewind), PERSISTED across restarts ---
    if collected.next_since and (since is None or collected.next_since > since):
        rt.store.set_cursor(repo, collected.next_since)
        rt.since_cursors[repo] = collected.next_since  # mirror the persisted value
    result.next_since = rt.store.get_cursor(repo)
    return result


def _update_issue_baseline(
    rt: PipelineRuntime, repo: str, observations: list[IssueObservation]
) -> None:
    """Persist this window's issue rate as the baseline for the next pass (AE1).

    The baseline is the observed per-second rate across the window spanned by the
    observations; a single observation (or zero time span) falls back to the
    cold-start window so the stored rate stays deterministic and non-zero-divide.
    """
    from .filter import COLD_START_WINDOW_SECONDS

    ts_values = [o.ts for o in observations if o.ts]
    if len(ts_values) >= 2:
        span = max(ts_values) - min(ts_values)
        window = span if span > 0 else COLD_START_WINDOW_SECONDS
    else:
        window = COLD_START_WINDOW_SECONDS
    rate = len(observations) / window if window > 0 else 0.0
    rt.store.set_issue_baseline(repo, rate, window)


# ---------------------------------------------------------------------------
# U4 — mode A supply-chain pipeline (resolve deps → advisories → dedup → alert)
# ---------------------------------------------------------------------------
@dataclass
class SecurityTickResult:
    """Audit record of one supply-chain tick (asserted by tests)."""

    alerts: int = 0  # new advisory alerts delivered
    advisories_seen: int = 0
    osv_degraded: bool = False
    notes: list[str] = field(default_factory=list)


def run_security_tick(
    rt: PipelineRuntime, repo: str, *, call_fn=llm.call
) -> SecurityTickResult:
    """Run one mode-A supply-chain pipeline pass for a single watchlist repo (U4).

    Flow: ``resolve_repo_dependencies`` (direct + manual edges; transitive only
    for published repos, AE3) → ``collect_supply_chain`` (OSV primary, GHSA
    malware atom fallback when OSV is degraded, R7) → per ``AdvisoryHit``,
    ``dedup_advisory`` suppresses repeats across cycles (AE4); a NEW hit →
    ``build_alert`` (its deterministic OSV/GHSA ID is the FACT — the LLM only adds
    short remediation advice; cross-validation is intrinsic) → IMMEDIATE delivery
    (F3 high-risk, never a digest wait).
    """
    token = _github_token(rt)
    api_key = _llm_api_key(rt)
    base_url = _llm_base_url(rt)
    result = SecurityTickResult()

    manual_edges = [
        edge.to_repo
        for edge in rt.settings.watchlist.dependency_edges
        if edge.from_repo == repo
    ]
    repo_deps = resolve_repo_dependencies(
        rt.client, repo, token=token, manual_edges=manual_edges
    )

    packages = [
        (d.name, d.version or "", d.ecosystem)
        for d in repo_deps.all_dependencies()
        if d.name
    ]
    watched_names = {d.name for d in repo_deps.all_dependencies() if d.name}

    hits, degraded = collect_supply_chain(
        rt.client, packages, watched_names=watched_names
    )
    result.osv_degraded = degraded
    if degraded:
        result.notes.append("OSV degraded → GHSA malware fallback (R7)")

    for hit in hits:
        result.advisories_seen += 1
        ghsa_id = next((i for i in hit.ids if i), "")
        if not ghsa_id:
            continue
        decision = dedup_advisory(rt.store, ghsa_id, repo)
        if not decision.is_new:
            continue  # same advisory across cycles is suppressed (AE4)
        report = build_alert(
            hit, repo=repo, api_key=api_key, base_url=base_url, call_fn=call_fn
        )
        # Force the immediate (high-risk) route regardless of report severity (F3).
        _deliver_report(rt, report, Severity.CRITICAL)
        result.alerts += 1

    return result


# ---------------------------------------------------------------------------
# U5 — mode B trend pipeline (collect trends → artifact diff → batch → digest)
# ---------------------------------------------------------------------------
@dataclass
class TrendTickResult:
    """Audit record of one trend tick (asserted by tests)."""

    delivered: int = 0  # explanation reports delivered to the digest
    skipped_unchanged: list[str] = field(default_factory=list)
    reaccelerated: list[str] = field(default_factory=list)
    degraded: bool = False
    notes: list[str] = field(default_factory=list)


def run_trend_tick(
    rt: PipelineRuntime,
    *,
    ts: float,
    snapshot_candidates: list[TrendCandidate] | None = None,
    language: str | None = None,
    call_fn=llm.call,
) -> TrendTickResult:
    """Run one mode-B trend pipeline pass for the configured scope (U5).

    Flow: ``collect_trends`` (OSS Insight primary, star-snapshot diff fallback
    when OSS Insight is unreachable — non-silent degradation, R6) → ``explain_trends``,
    which performs the artifact-diff dedup against the seen-set internally
    (``artifact_fingerprint`` vs ``has_seen``/``mark_seen``; unchanged content is
    skipped, a significant re-acceleration re-enters — AE2) and makes ONE cheap
    batched LLM call (KTD7) → each surviving explanation report is delivered to
    the DIGEST route (R16). Injection in any candidate's README is isolated to the
    data slot AND per-candidate (no batch cross-contamination) by ``explain_trends``.
    """
    api_key = _llm_api_key(rt)
    base_url = _llm_base_url(rt)
    result = TrendTickResult()

    scope = rt.settings.trend_scope
    collected = collect_trends(
        rt.client,
        rt.store,
        topics=scope.topics,
        keywords=scope.keywords,
        language=language,
        snapshot_candidates=snapshot_candidates,
        ts=ts,
    )
    result.degraded = collected.degraded
    result.notes.extend(collected.warnings)

    outcome = explain_trends(
        collected.candidates,
        rt.store,
        api_key=api_key,
        base_url=base_url,
        call_fn=call_fn,
        embed_fn=_embed_fn(rt),
    )
    result.skipped_unchanged = list(outcome.skipped_unchanged)
    result.reaccelerated = list(outcome.reaccelerated)

    for report in outcome.reports:
        # Trend explanations are low-priority → digest route (NORMAL urgency).
        _deliver_report(rt, report, Severity.NORMAL)
        result.delivered += 1

    return result
