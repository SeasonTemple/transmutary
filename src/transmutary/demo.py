"""Offline, zero-credential demo of the Transmutary pipeline (U1-U3, KTD-A..E).

Run ``transmutary-demo`` (after ``pip install -e .``) to watch one full pass of
all three pipeline ticks - release/issue, supply-chain, and trend - WITHOUT any
real network call, any real LLM call, or any real credential.

How the "zero real IO" guarantee holds (the plan's hard constraints):

  * **No real network (KTD-B)** - every outbound HTTP request is intercepted by an
    ``httpx.MockTransport`` whose handler (:func:`_demo_handler`) routes each URL to
    a built-in, realistic-looking fake response (GitHub releases atom / REST issues
    / package.json contents, OSV ``querybatch``, OSS Insight trending). The mock
    client is injected into ``build_runtime(client=...)``; the production
    ``make_client()`` is never constructed, so nothing can reach the wire.
  * **No real LLM (KTD-B)** - the ticks are driven with a stub ``call_fn``
    (:func:`_stub_call`) that dispatches on the trusted system instruction and
    returns deterministic fake report text, and ``embed_fn=None`` (L2 disabled), so
    ``llm.call`` / ``llm.embed`` / litellm are never touched.
  * **No real credentials (KTD-B)** - a :class:`~transmutary.config.Credentials`
    object is built from obvious placeholder values; they are never used to reach
    anything real because of the two stubs above.
  * **Reuses the real pipeline seams (KTD-A)** - the demo composes the SHIPPED
    ``build_runtime`` + ``run_release_issue_tick`` / ``run_security_tick`` /
    ``run_trend_tick`` through their ``store`` / ``client`` / ``call_fn`` /
    ``embed_fn`` injection points. It does NOT re-implement any pipeline stage.
  * **Contained artifacts, permission guards intact (KTD-C)** - artifacts land in a
    fresh ``tempfile.mkdtemp()`` directory (or an explicit ``--out`` dir); the real
    ``ArtifactStore`` 0700/0600 guards are exercised unchanged.
  * **Independent module (KTD-E)** - this file only imports the public pipeline /
    config interfaces and carries its own mock data + stubs. Nothing in the
    production path imports it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile

import httpx

from .config import (
    Credentials,
    Delivery,
    DependencyEdge,
    RepoEntry,
    Settings,
    TrendScope,
    Watchlist,
)
from .deliver.routes import DeliveryRoute
from .pipeline import (
    build_runtime,
    run_release_issue_tick,
    run_security_tick,
    run_trend_tick,
)
from .store.state import StateStore

# ---------------------------------------------------------------------------
# Demo watchlist / scope (realistic-looking, mode-A subjects + a dependency edge)
# ---------------------------------------------------------------------------
# A maintained upstream CLI that routes through an internal gateway - the canonical
# CONTEXT example of a declared dependency edge (runtime service link, F1).
_DEMO_REPO = "octocat/hexbridge-cli"
_DEMO_DEP_REPO = "octocat/hexbridge-gateway"


# ---------------------------------------------------------------------------
# Built-in mock data (realistic-looking - believable repos / tags / advisories)
# ---------------------------------------------------------------------------
def _releases_atom(repo: str) -> str:
    """A GitHub ``releases.atom`` feed with one believable published release."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Release notes from {repo}</title>
  <entry>
    <id>tag:github.com,2008:Repository/1/v2.4.0</id>
    <title>v2.4.0</title>
    <updated>2026-05-29T14:02:00Z</updated>
    <link rel="alternate" type="text/html"
          href="https://github.com/{repo}/releases/tag/v2.4.0"/>
    <content type="html">v2.4.0 switches the default transport to HTTP/2 and
    drops the legacy retry shim. Connection pooling is now on by default; set
    HEXBRIDGE_HTTP1=1 to opt out. Fixes a deadlock under high concurrency.</content>
  </entry>
</feed>"""


# Five near-duplicate "outage" issues so the issue-surge filter trips its
# cold-start floor and the L3 triage judge confirms a fault (a believable
# incident: a new release breaking downstream callers via the gateway).
_DEMO_ISSUES = [
    {
        "number": 412,
        "title": "504 from gateway after upgrading to v2.4.0",
        "body": "Since v2.4.0 the CLI returns 504 Gateway Timeout against our "
        "internal gateway. Rolling back to v2.3.5 fixes it. Looks like the new "
        "HTTP/2 default is incompatible with our proxy.",
        "html_url": f"https://github.com/{_DEMO_REPO}/issues/412",
        "updated_at": "2026-05-29T15:10:00Z",
        "state": "open",
    },
    {
        "number": 413,
        "title": "Timeouts / 504 outage on 2.4.0 in production",
        "body": "Confirmed outage: every request times out (504) after the 2.4.0 "
        "bump. Service is effectively down for us. HTTP/2 transport seems to hang.",
        "html_url": f"https://github.com/{_DEMO_REPO}/issues/413",
        "updated_at": "2026-05-29T15:24:00Z",
        "state": "open",
    },
    {
        "number": 414,
        "title": "v2.4.0 deadlock under load - connections never released",
        "body": "Under concurrency the new connection pool deadlocks and we see a "
        "504 outage. The legacy retry shim removal looks related.",
        "html_url": f"https://github.com/{_DEMO_REPO}/issues/414",
        "updated_at": "2026-05-29T15:41:00Z",
        "state": "open",
    },
    {
        "number": 415,
        "title": "Production down: 504 spikes immediately after deploy of 2.4.0",
        "body": "We deployed 2.4.0 and the gateway started returning 504 within "
        "minutes. Reverting restored service. This is an active outage.",
        "html_url": f"https://github.com/{_DEMO_REPO}/issues/415",
        "updated_at": "2026-05-29T16:02:00Z",
        "state": "open",
    },
    {
        "number": 416,
        "title": "2.4.0 HTTP/2 default causes 504 against reverse proxies",
        "body": "The HTTP/2 default in 2.4.0 is incompatible with our reverse "
        "proxy and triggers a 504 outage. Please document HEXBRIDGE_HTTP1=1.",
        "html_url": f"https://github.com/{_DEMO_REPO}/issues/416",
        "updated_at": "2026-05-29T16:20:00Z",
        "state": "open",
    },
]

# A believable npm manifest for the watched CLI (drives the supply-chain tick).
_DEMO_PACKAGE_JSON = {
    "name": "hexbridge-cli",
    "version": "2.4.0",
    "dependencies": {"ansi-regex": "5.0.0", "node-fetch": "2.6.6"},
    "devDependencies": {"typescript": "5.4.0"},
}

# A believable OSV advisory hit on one of the deps (a real-world flavored GHSA id).
_DEMO_OSV_RESULTS = {
    "results": [
        {
            "vulns": [
                {
                    "id": "GHSA-93q8-gq69-wqmw",
                    "summary": "Inefficient Regular Expression Complexity in "
                    "ansi-regex (ReDoS). A crafted string can cause "
                    "catastrophic backtracking and hang the event loop.",
                }
            ]
        },
        {},  # node-fetch: no advisory in this demo
    ]
}

# A believable OSS Insight trending payload (mode B discovery). Two near-duplicate
# "agent framework" repos + one distinct "vector db" repo, all in the AI scope.
_DEMO_TRENDING_ROWS = [
    {
        "repo_name": "lumen-labs/agentflow",
        "stars": 8421,
        "stars_increment": 640,
        "description": "An open-source framework for building autonomous LLM "
        "agents with tool use, planning, and long-term memory.",
        "language": "Python",
        "html_url": "https://github.com/lumen-labs/agentflow",
    },
    {
        "repo_name": "synaptik/agent-runtime",
        "stars": 5310,
        "stars_increment": 512,
        "description": "A lightweight runtime for orchestrating LLM agents: "
        "tool calling, planning loops, and memory - batteries included.",
        "language": "Python",
        "html_url": "https://github.com/synaptik/agent-runtime",
    },
    {
        "repo_name": "vectoria/quill-db",
        "stars": 3990,
        "stars_increment": 410,
        "description": "A fast embedded vector database for AI/LLM retrieval, with "
        "hybrid keyword + semantic search and on-disk persistence.",
        "language": "Rust",
        "html_url": "https://github.com/vectoria/quill-db",
    },
]


# ---------------------------------------------------------------------------
# Mock httpx transport (KTD-B - every outbound request is intercepted here)
# ---------------------------------------------------------------------------
def _b64_contents(obj: dict) -> httpx.Response:
    encoded = base64.b64encode(json.dumps(obj).encode()).decode()
    return httpx.Response(200, json={"content": encoded, "encoding": "base64"})


def _demo_handler(request: httpx.Request) -> httpx.Response:
    """Route every demo request to a built-in fake response (no real network).

    The URL grammar mirrors the production collectors' constructed URLs (see
    ``collect/github.py``, ``collect/security.py``, ``collect/trend.py``); each
    branch returns a shape the matching collector already knows how to parse.
    """
    url = str(request.url)

    # OSS Insight trending (mode B primary). Matched before the generic atom check.
    if "ossinsight" in url:
        return httpx.Response(200, json={"data": {"rows": _DEMO_TRENDING_ROWS}})

    # OSV supply-chain querybatch (deterministic advisory hit).
    if "querybatch" in url:
        return httpx.Response(200, json=_DEMO_OSV_RESULTS)

    # GHSA malware atom fallback (only hit if OSV degraded - empty here).
    if "advisories.atom" in url:
        return httpx.Response(200, text="<feed xmlns='http://www.w3.org/2005/Atom'></feed>")

    # GitHub releases atom (published releases).
    if "releases.atom" in url or url.endswith(".atom"):
        return httpx.Response(200, text=_releases_atom(_repo_of(url)))

    # GitHub Contents API: package.json (supply-chain dependency manifest).
    if "/contents/package.json" in url:
        return _b64_contents(_DEMO_PACKAGE_JSON)

    # GitHub REST pre-release backfill: none in this demo.
    if "/releases" in url:
        return httpx.Response(200, json=[])

    # GitHub REST issues. The dependency-edge "related" repo collect (gateway) also
    # lands here; give it one believable corroborating issue so the diagnosis shows
    # related-repo context (F1), while the primary CLI repo gets the outage surge.
    if "/issues" in url:
        if _DEMO_DEP_REPO in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 88,
                        "title": "Gateway rejects HTTP/2 upgrade from CLI clients",
                        "body": "The gateway closes HTTP/2 connections from "
                        "hexbridge-cli 2.4.0, surfacing as 504 to callers.",
                        "html_url": f"https://github.com/{_DEMO_DEP_REPO}/issues/88",
                        "updated_at": "2026-05-29T16:30:00Z",
                        "state": "open",
                    }
                ],
            )
        return httpx.Response(200, json=_DEMO_ISSUES)

    # Anything unexpected -> 404 (and it would still never leave the process).
    return httpx.Response(404)


def _repo_of(url: str) -> str:
    """Best-effort owner/name extraction from a github.com atom URL (cosmetic)."""
    try:
        path = httpx.URL(url).path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    except (ValueError, TypeError):
        pass
    return _DEMO_REPO


def make_mock_client() -> httpx.Client:
    """Build the SSRF-contract-compliant mock client (redirects OFF, KTD-B)."""
    return httpx.Client(
        transport=httpx.MockTransport(_demo_handler), follow_redirects=False
    )


# ---------------------------------------------------------------------------
# Stub LLM (KTD-B - deterministic fake report text, never touches a real model)
# ---------------------------------------------------------------------------
def _stub_call(system, data, tier=None, *, api_key=None, base_url=None, **kwargs):
    """A deterministic ``call_fn`` seam standing in for ``llm.call``.

    Dispatch is on the TRUSTED system instruction only (never the untrusted data
    block): the triage judge confirms the outage surge, the diagnostician and the
    trend explainer emit believable report bodies, and the remediation assistant
    returns short advice. Anything else returns an inert default.
    """
    sys_l = (system or "").lower()

    # L3 triage judge (issue-surge filter): confirm this is a genuine fault so the
    # demo produces a diagnosis report. Must be well-formed JSON the filter parses.
    if "triage judge" in sys_l:
        return (
            '{"is_fault": true, "reason": "Multiple independent reports of a 504 '
            'outage immediately after the v2.4.0 deploy indicate a genuine fault."}'
        )

    # Mode A sourcing diagnostician (diagnose.py).
    if "sourcing diagnostician" in sys_l:
        return (
            "## Suspected root cause\n"
            "The v2.4.0 release switched the default transport to HTTP/2 and removed "
            "the legacy retry shim. Downstream callers behind an HTTP/1-only reverse "
            "proxy / gateway now receive 504 Gateway Timeout under load.\n\n"
            "## Affected dependencies\n"
            "hexbridge-cli >= 2.4.0 when routed through an HTTP/1 gateway.\n\n"
            "## Related repositories (via dependency edges)\n"
            "octocat/hexbridge-gateway reports rejecting HTTP/2 upgrades from CLI "
            "clients - the same incident seen from the gateway side.\n\n"
            "## Recommended actions\n"
            "Pin to v2.3.5 or set HEXBRIDGE_HTTP1=1 until the gateway supports "
            "HTTP/2; track the upstream fix for the connection-pool deadlock."
        )

    # Mode B trend explainer (explain.py) - must return a JSON array keyed by index.
    if "trend explainer" in sys_l:
        return json.dumps(
            [
                {
                    "index": 0,
                    "summary": "An open-source framework for building autonomous "
                    "LLM agents (tool use, planning, memory). Trending as teams "
                    "move from single prompts to multi-step agent workflows.",
                },
                {
                    "index": 1,
                    "summary": "A fast embedded vector database for LLM retrieval "
                    "with hybrid keyword + semantic search. Rising alongside the "
                    "shift toward local, on-disk RAG stores.",
                },
            ]
        )

    # Supply-chain remediation assistant (security.py build_alert).
    if "remediation" in sys_l:
        return (
            "Upgrade ansi-regex to >= 5.0.1 (or >= 6.0.1), which fixes the ReDoS. "
            "Pin the resolved version and re-run your lockfile audit to confirm no "
            "transitive copy remains."
        )

    return "(demo stub: no report generated for this stage)"


# ---------------------------------------------------------------------------
# Fake config / credentials (KTD-B - obvious placeholders, never really used)
# ---------------------------------------------------------------------------
def _demo_credentials() -> Credentials:
    """Build a Credentials object from obvious placeholder values (never used)."""
    return Credentials(
        github_token="ghp_DEMO_fake_token_000000000000000000",
        smtp_user="demo@example.invalid",
        smtp_password="demo-not-a-real-password",
        rss_token="demo-rss-token-not-real",
        llm_api_key="sk-DEMO_fake_llm_key_0000000000000000",
    )


def _demo_settings(artifact_root: str, state_db_path: str) -> Settings:
    """Build a self-contained demo Settings (watchlist + scope + delivery)."""
    return Settings(
        watchlist=Watchlist(
            repos=[RepoEntry(repo=_DEMO_REPO), RepoEntry(repo=_DEMO_DEP_REPO)],
            dependency_edges=[
                DependencyEdge(from_repo=_DEMO_REPO, to_repo=_DEMO_DEP_REPO)
            ],
        ),
        trend_scope=TrendScope(
            topics=["ai", "llm"],
            keywords=["llm", "agent", "vector", "ai"],
        ),
        delivery=Delivery(
            state_db_path=state_db_path,
            artifact_root=artifact_root,
            token_max_age_days=90,
            digest_hour=9,
            # RSS-only deployment for the demo (no email leg) - feed_dir defaults to
            # <artifact_root>/_feed inside build_runtime.
            email_recipients=[],
            smtp_host=None,
            feed_dir=None,
        ),
        # A placeholder gateway base_url (non-secret config; never reached because
        # the LLM is stubbed).
        llm_base_url="https://llm-gateway.example.invalid/v1",
    )


# ---------------------------------------------------------------------------
# Output: walk the artifact root + print a readable summary
# ---------------------------------------------------------------------------
def _print_tree(artifact_root: str) -> list[str]:
    """Print the artifact tree under ``artifact_root``; return the file paths."""
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(artifact_root):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, artifact_root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        indent = "  " * depth
        if rel_dir == ".":
            label = os.path.basename(artifact_root.rstrip(os.sep))
        else:
            label = os.path.basename(dirpath)
        print(f"{indent}{label}/")
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            paths.append(full)
            print(f"{indent}  {fname}")
    return paths


def _print_report_excerpt(path: str, *, max_lines: int = 14) -> None:
    """Print the head of one rendered report file (a believable excerpt)."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:  # pragma: no cover - defensive
        print(f"  (could not read {path}: {exc})")
        return
    print(f"\n--- {os.path.relpath(path)} ---")
    for line in lines[:max_lines]:
        print(f"  {line}")
    if len(lines) > max_lines:
        print(f"  ... ({len(lines) - max_lines} more lines)")


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Run one offline pass of all three ticks and print the artifacts + summary.

    Returns a process exit code (0 on success). Zero real network, zero real LLM,
    zero real credentials - see the module docstring for how each is guaranteed.
    """
    parser = argparse.ArgumentParser(
        prog="transmutary-demo",
        description="Offline, zero-credential demo of the Transmutary pipeline.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Artifact output directory (default: a fresh temp dir; kept on exit "
        "so you can inspect it).",
    )
    args = parser.parse_args(argv)

    if args.out:
        artifact_root = os.path.abspath(args.out)
        os.makedirs(artifact_root, mode=0o700, exist_ok=True)
        os.chmod(artifact_root, 0o700)
    else:
        artifact_root = tempfile.mkdtemp(prefix="transmutary-demo-")

    print("=" * 72)
    print("Transmutary - offline demo (zero credentials, zero network, zero LLM)")
    print("=" * 72)
    print(f"Artifacts -> {artifact_root}\n")

    # In-memory state store keeps the demo self-contained; the artifacts themselves
    # land on disk under artifact_root.
    settings = _demo_settings(artifact_root, state_db_path=":memory:")
    creds = _demo_credentials()
    client = make_mock_client()
    store = StateStore(":memory:")

    # KTD-A: compose the SHIPPED runtime + ticks through their injection seams.
    rt = build_runtime(settings, creds, client=client, store=store)

    # --- Tick 1: mode A release/issue (release diagnosis + issue-surge diagnosis) ---
    ri = run_release_issue_tick(rt, _DEMO_REPO, call_fn=_stub_call, embed_fn=None)
    print("[1/3] release/issue tick")
    print(
        f"      new releases diagnosed: {ri.releases_new} | "
        f"issue surge triggered: {ri.issue_triggered} | "
        f"reports delivered: {ri.diagnosed}"
    )

    # --- Tick 2: mode A supply-chain (deterministic OSV hit -> immediate alert) ---
    sec = run_security_tick(rt, _DEMO_REPO, call_fn=_stub_call)
    print("[2/3] supply-chain tick")
    print(
        f"      advisories seen: {sec.advisories_seen} | "
        f"alerts delivered: {sec.alerts} | osv degraded: {sec.osv_degraded}"
    )

    # --- Tick 3: mode B trend radar (trending -> batch summary -> digest) ---
    trend = run_trend_tick(rt, ts=1_750_000_000.0, call_fn=_stub_call, embed_fn=None)
    print("[3/3] trend tick")
    print(
        f"      trend reports delivered: {trend.delivered} | "
        f"skipped unchanged: {len(trend.skipped_unchanged)} | "
        f"degraded: {trend.degraded}"
    )

    # --- Walk the produced artifacts ---
    print("\n" + "-" * 72)
    print("Artifact tree (per-repo archive + _delivered/<route>/ + _feed/<route>.atom.xml)")
    print("-" * 72)
    paths = _print_tree(artifact_root)

    # --- Show one diagnosis + one trend report excerpt so the output is tangible ---
    immediate_sep = os.sep + "_delivered" + os.sep + DeliveryRoute.IMMEDIATE.value + os.sep
    digest_sep = os.sep + "_delivered" + os.sep + DeliveryRoute.DIGEST.value + os.sep
    delivered_immediate = sorted(p for p in paths if immediate_sep in p)
    delivered_digest = sorted(p for p in paths if digest_sep in p)
    print("\n" + "-" * 72)
    print("Report excerpts")
    print("-" * 72)
    if delivered_immediate:
        _print_report_excerpt(delivered_immediate[0])
    if delivered_digest:
        _print_report_excerpt(delivered_digest[0])

    feeds = sorted(p for p in paths if p.endswith(".atom.xml"))
    total_delivered = ri.diagnosed + sec.alerts + trend.delivered
    print("\n" + "-" * 72)
    print(
        f"Summary: {total_delivered} report(s) delivered across "
        f"{len(feeds)} RSS feed(s); {len(paths)} artifact file(s) on disk."
    )
    print("All output above came from built-in mock data + stub LLM - no real IO.")
    print("-" * 72)

    return 0


if __name__ == "__main__":  # pragma: no cover - module entry
    sys.exit(main())
