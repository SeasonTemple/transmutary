"""Dashboard tests — view-data read layer (U2) and the Starlette app (U3/U4).

Fully offline: in-memory StateStore + a tmp ArtifactStore + Starlette TestClient.
No real network / uvicorn / LLM (matches the project test invariants)."""

from __future__ import annotations

import dataclasses
import os
import tempfile

import pytest

from transmutary.config import (
    Delivery,
    RepoEntry,
    Settings,
    TrendScope,
    Watchlist,
)
from transmutary.report.schema import Report, ReportKind, Severity, Source
from transmutary.store.artifacts import ArtifactStore
from transmutary.store.state import StateStore


def _settings(repos=("acme/cli", "acme/gateway"), artifact_root="/tmp/x") -> Settings:
    return Settings(
        watchlist=Watchlist(repos=[RepoEntry(repo=r) for r in repos], dependency_edges=[]),
        trend_scope=TrendScope(topics=["ai"], keywords=["llm"]),
        delivery=Delivery(state_db_path=":memory:", artifact_root=artifact_root),
    )


def _report(repo, *, kind=ReportKind.DIAGNOSE, severity=Severity.HIGH, sources=()):
    return Report(
        title=f"Report for {repo}",
        kind=kind,
        repo=repo,
        severity=severity,
        body_md="some body text",
        created_at="2026-01-01T00:00:00Z",
        sources=sources,
    )


def _flatten_keys(obj):
    """All nested dataclass field names + string values, for credential audits."""
    out = []
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            out.append(f.name)
            out.extend(_flatten_keys(getattr(obj, f.name)))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            out.extend(_flatten_keys(item))
    elif isinstance(obj, str):
        out.append(obj)
    return out


# --- _safe_url (R-D15) -------------------------------------------------------


def test_safe_url_blanks_dangerous_schemes():
    from transmutary.dashboard import data

    assert data._safe_url("https://example.com/x") == "https://example.com/x"
    assert data._safe_url("http://example.com") == "http://example.com"
    assert data._safe_url("javascript:alert(1)") is None
    assert data._safe_url("JavaScript:alert(1)") is None
    assert data._safe_url("data:text/html;base64,xx") is None
    assert data._safe_url("") is None
    assert data._safe_url(None) is None


# --- build_watchlist (R-D1) --------------------------------------------------


def test_build_watchlist_tags_sources():
    from transmutary.dashboard import data

    store = StateStore(":memory:")
    store.promote_repo("hot/repo", source="mode-b")
    entries = data.build_watchlist(_settings(), store)
    by_repo = {e.repo: e.source for e in entries}
    assert by_repo["acme/cli"] == "config"
    assert by_repo["acme/gateway"] == "config"
    assert by_repo["hot/repo"] == "mode-b"


# --- build_overview (R-D2/R-D3/R-D4) -----------------------------------------


def test_build_overview_buckets_and_orders():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        store = StateStore(":memory:")
        artifacts = ArtifactStore(root)
        artifacts.write(_report("acme/cli", severity=Severity.LOW), ts=1000.0)
        artifacts.write(
            _report("acme/cli", severity=Severity.MALWARE), ts=3000.0
        )
        artifacts.write(
            _report("acme/gateway", kind=ReportKind.EXPLAIN, severity=Severity.INFO),
            ts=2000.0,
        )
        ov = data.build_overview(_settings(artifact_root=root), store, artifacts)

        # recent: newest first across repos
        assert [c.ts for c in ov.recent_reports] == [3000, 2000, 1000]
        # supply-chain alerts: only malware/critical
        assert [c.ts for c in ov.supply_chain_alerts] == [3000]
        # trend candidates: explain kind
        assert [c.ts for c in ov.trend_candidates] == [2000]
        # feed links are local & token-free
        assert {f.href for f in ov.feeds} == {"/feed/immediate", "/feed/digest"}
        for f in ov.feeds:
            assert "token" not in f.href.lower()


def test_build_overview_empty_store():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        store = StateStore(":memory:")
        artifacts = ArtifactStore(root)
        ov = data.build_overview(_settings(artifact_root=root), store, artifacts)
        assert ov.recent_reports == ()
        assert ov.supply_chain_alerts == ()
        assert ov.trend_candidates == ()
        assert len(ov.watchlist) == 2  # config repos still listed


# --- build_repo_runtime (R-D5) -----------------------------------------------


def test_build_repo_runtime_known_and_unknown():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        store = StateStore(":memory:")
        artifacts = ArtifactStore(root)
        artifacts.write(_report("acme/cli"), ts=1000.0)
        store.set_issue_baseline("acme/cli", 2.5, 3600.0)
        store.add_star_snapshot("acme/cli", 100, ts=1.0)
        store.add_star_snapshot("acme/cli", 180, ts=2.0)
        store.set_cursor("acme/cli", "2026-01-01")

        result = data.build_repo_runtime(_settings(artifact_root=root), store, artifacts, "acme/cli")
        assert result is not None
        runtime, cards = result
        assert runtime.in_watchlist is True
        assert runtime.baseline_rate == 2.5
        assert runtime.latest_stars == 180
        assert runtime.star_growth == 80
        assert runtime.cursor == "2026-01-01"
        assert len(cards) == 1

        # unknown repo with no archive → None (no oracle)
        s = _settings(artifact_root=root)
        assert data.build_repo_runtime(s, store, artifacts, "no/repo") is None


# --- build_report_view + R-D15 source sanitisation ---------------------------


def test_build_report_view_sanitises_source_urls():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        artifacts = ArtifactStore(root)
        sources = (
            Source(source_id="ok", url="https://safe.example/x", fetched_at="2026-01-01"),
            Source(source_id="evil", url="javascript:alert(1)", fetched_at="2026-01-01"),
        )
        artifacts.write(_report("acme/cli", sources=sources), ts=1000.0)
        view = data.build_report_view(artifacts, "acme/cli", "1000-diagnose.md")
        assert view is not None
        by_id = {s.source_id: s.url for s in view.sources}
        assert by_id["ok"] == "https://safe.example/x"
        assert by_id["evil"] is None  # R-D15: javascript: blanked
        assert view.card.title == "Report for acme/cli"

        assert data.build_report_view(artifacts, "acme/cli", "9999-diagnose.md") is None


# --- R-D9: no credentials / tokens in any view model -------------------------


def test_view_models_carry_no_tokens_or_credentials():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        store = StateStore(":memory:")
        artifacts = ArtifactStore(root)
        # A subscriber token exists in the store — it must NEVER reach a view model.
        secret = "super-secret-token-value"
        from transmutary.deliver.server import hash_token

        store.add_subscriber_token(hash_token(secret), "alice")
        artifacts.write(_report("acme/cli"), ts=1000.0)

        ov = data.build_overview(_settings(artifact_root=root), store, artifacts)
        view = data.build_report_view(artifacts, "acme/cli", "1000-diagnose.md")

        flat = _flatten_keys(ov) + _flatten_keys(view)
        joined = " ".join(flat).lower()
        assert secret not in joined
        assert hash_token(secret) not in joined
        assert "token" not in joined
        assert "api_key" not in joined
        assert "password" not in joined
