"""U12 trend.py tests — OSS Insight trending, star-snapshot fallback, scope
filter, first-run no-growth, degradation alert, SSRF allowlist (R4/R6/R23, F2).

All HTTP mocked via httpx.MockTransport; state via in-memory sqlite. No real
network.
"""

from __future__ import annotations

import httpx
import pytest

from transmutary.collect import trend
from transmutary.collect.github import SSRFError
from transmutary.collect.trend import (
    TrendCandidate,
    assert_candidate_url_allowed,
    collect_trends,
    filter_scope,
    in_scope,
    snapshot_growth,
)
from transmutary.store.state import StateStore

TOPICS = ["llm", "machine-learning"]
KEYWORDS = ["agent", "inference"]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _store() -> StateStore:
    return StateStore(":memory:")


# OSS Insight-shaped trending payload (data.rows).
def _trending_payload(rows: list[dict]) -> dict:
    return {"data": {"rows": rows}}


# --- Happy: trending + growth → new/accelerating with growth rate -------------
def test_happy_trending_with_growth():
    rows = [
        {
            "repo_name": "acme/agent-kit",
            "stars": 1200,
            "stars_increment": 300,
            "topics": ["llm"],
            "description": "An LLM agent framework",
            "html_url": "https://github.com/acme/agent-kit",
        }
    ]

    def handler(request):
        assert request.url.host == "api.ossinsight.io"
        return httpx.Response(200, json=_trending_payload(rows))

    store = _store()
    with _client(handler) as client:
        result = collect_trends(
            client, store, topics=TOPICS, keywords=KEYWORDS, ts=1000.0
        )
    assert result.degraded is False
    assert len(result.candidates) == 1
    cand = result.candidates[0]
    assert cand.repo == "acme/agent-kit"
    assert cand.growth_per_day == 300.0  # OSS Insight period metric
    assert cand.growth_source == "ossinsight"


def test_snapshot_diff_backfills_growth_when_no_period_metric():
    # No stars_increment → growth backfilled from the star-snapshot diff.
    rows1 = [{"repo_name": "a/llm-tool", "stars": 100, "topics": ["llm"],
              "description": "llm tool"}]
    rows2 = [{"repo_name": "a/llm-tool", "stars": 200, "topics": ["llm"],
              "description": "llm tool"}]
    store = _store()

    def h1(request):
        return httpx.Response(200, json=_trending_payload(rows1))

    def h2(request):
        return httpx.Response(200, json=_trending_payload(rows2))

    with _client(h1) as c1:
        r1 = collect_trends(c1, store, topics=TOPICS, keywords=KEYWORDS, ts=0.0)
    # First snapshot → no growth this run.
    assert r1.candidates[0].growth_per_day is None
    with _client(h2) as c2:
        r2 = collect_trends(c2, store, topics=TOPICS, keywords=KEYWORDS, ts=86400.0)
    # 100 stars over 1 day → 100/day from snapshot diff.
    assert r2.candidates[0].growth_per_day == 100.0
    assert r2.candidates[0].growth_source == "snapshot-diff"


# --- F2: scope filter keeps AI-range, drops non-AI ---------------------------
def test_scope_filter_keeps_ai_drops_non_ai():
    ai_topic = TrendCandidate(repo="x/ai", topics=["machine-learning"], description="x")
    ai_kw = TrendCandidate(repo="y/agent-runner", description="an agent orchestrator")
    non_ai = TrendCandidate(repo="z/css-toolkit", topics=["css"], description="styling")
    kept = filter_scope([ai_topic, ai_kw, non_ai], topics=TOPICS, keywords=KEYWORDS)
    repos = {c.repo for c in kept}
    assert repos == {"x/ai", "y/agent-runner"}
    assert non_ai.repo not in repos


def test_in_scope_topic_or_keyword():
    assert in_scope(TrendCandidate(repo="r", topics=["LLM"]), topics=TOPICS, keywords=KEYWORDS)
    assert in_scope(
        TrendCandidate(repo="r", description="fast inference engine"),
        topics=TOPICS, keywords=KEYWORDS,
    )
    assert not in_scope(
        TrendCandidate(repo="r", topics=["web"], description="frontend"),
        topics=TOPICS, keywords=KEYWORDS,
    )


def test_collect_filters_non_ai_from_trending():
    rows = [
        {"repo_name": "a/llm-x", "stars": 10, "topics": ["llm"], "description": "llm"},
        {"repo_name": "b/css-x", "stars": 10, "topics": ["css"], "description": "styles"},
    ]

    def handler(request):
        return httpx.Response(200, json=_trending_payload(rows))

    store = _store()
    with _client(handler) as client:
        result = collect_trends(client, store, topics=TOPICS, keywords=KEYWORDS, ts=1.0)
    assert [c.repo for c in result.candidates] == ["a/llm-x"]


# --- Edge: OSS Insight down → snapshot fallback + non-silent alert ------------
def test_ossinsight_down_falls_back_with_warning():
    def handler(request):
        return httpx.Response(503, text="service unavailable")

    store = _store()
    # Seed a prior snapshot so the fallback can compute growth.
    store.add_star_snapshot("a/llm-tool", 100, ts=0.0)
    snap_cands = [TrendCandidate(repo="a/llm-tool", stargazers=300, topics=["llm"],
                                 description="llm tool")]
    with _client(handler) as client:
        result = collect_trends(
            client, store, topics=TOPICS, keywords=KEYWORDS,
            snapshot_candidates=snap_cands, ts=86400.0,
        )
    assert result.degraded is True
    assert result.warnings  # NOT silent
    assert any("fallback" in w.lower() for w in result.warnings)
    assert len(result.candidates) == 1
    assert result.candidates[0].growth_per_day == 200.0
    assert result.candidates[0].growth_source == "snapshot-diff"


def test_ossinsight_unreachable_transport_error_degrades():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    store = _store()
    with _client(handler) as client:
        result = collect_trends(
            client, store, topics=TOPICS, keywords=KEYWORDS,
            snapshot_candidates=[], ts=1.0,
        )
    assert result.degraded is True
    assert result.warnings


# --- Edge: first run, no history → record snapshot, no growth -----------------
def test_first_snapshot_no_growth():
    store = _store()
    growth = snapshot_growth(store, "a/repo", 500, ts=100.0)
    assert growth is None
    snaps = store.get_star_snapshots("a/repo")
    assert len(snaps) == 1
    assert snaps[0].stargazers == 500


def test_second_snapshot_yields_growth():
    store = _store()
    snapshot_growth(store, "a/repo", 100, ts=0.0)
    growth = snapshot_growth(store, "a/repo", 250, ts=86400.0)
    assert growth == 150.0


def test_non_positive_dt_no_growth():
    store = _store()
    snapshot_growth(store, "a/repo", 100, ts=100.0)
    # Same/earlier ts → no spurious rate.
    assert snapshot_growth(store, "a/repo", 200, ts=100.0) is None


# --- R23: SSRF allowlist — injected off-allowlist URL rejected, not fetched ---
def test_injected_off_allowlist_url_rejected_not_fetched():
    # A trending row injects an off-allowlist html_url (e.g. an internal metadata
    # endpoint). It must be dropped (not stored, never fetched) with a warning.
    rows = [{
        "repo_name": "evil/llm",
        "stars": 10,
        "topics": ["llm"],
        "description": "llm agent",
        "html_url": "http://169.254.169.254/latest/meta-data/",
    }]
    requested_hosts = []

    def handler(request):
        requested_hosts.append(request.url.host)
        return httpx.Response(200, json=_trending_payload(rows))

    store = _store()
    with _client(handler) as client:
        result = collect_trends(client, store, topics=TOPICS, keywords=KEYWORDS, ts=1.0)
    # Only the OSS Insight host was ever contacted (no SSRF fetch).
    assert requested_hosts == ["api.ossinsight.io"]
    # The off-allowlist URL was dropped from the stored candidate.
    assert result.candidates[0].url == ""
    assert any("off-allowlist" in w.lower() for w in result.warnings)


def test_assert_candidate_url_allowed():
    assert_candidate_url_allowed("https://github.com/a/b")  # on-allowlist OK
    with pytest.raises(SSRFError):
        assert_candidate_url_allowed("https://evil.example.com/x")


def test_redirect_following_client_refused():
    client = httpx.Client(follow_redirects=True)
    store = _store()
    with pytest.raises(SSRFError):
        collect_trends(client, store, topics=TOPICS, keywords=KEYWORDS, ts=1.0)
    client.close()


def test_off_allowlist_endpoint_not_contacted():
    # The collector itself only ever targets the OSS Insight allowlist host; if a
    # bad base URL host were used it would be rejected before any IO.
    def handler(request):
        return httpx.Response(200, json=_trending_payload([]))

    store = _store()
    with _client(handler) as client:
        # Directly assert the module endpoint is on the allowlist.
        assert httpx.URL(trend._OSSINSIGHT_TRENDING_URL).host in trend.ALLOWED_HOSTS
        collect_trends(client, store, topics=TOPICS, keywords=KEYWORDS, ts=1.0)
