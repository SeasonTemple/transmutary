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
        delivery=Delivery(
            state_db_path=":memory:",
            artifact_root=artifact_root,
            token_max_age_days=90,
            digest_hour=9,
        ),
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

    by_entry = {e.repo: e for e in entries}
    assert by_entry["acme/cli"].demotable is False
    assert by_entry["hot/repo"].demotable is True
    assert by_entry["hot/repo"].to_dict()["demotable"] is True


# --- build_overview (R-D2/R-D3/R-D4) -----------------------------------------


def test_build_overview_buckets_and_orders():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        store = StateStore(":memory:")
        artifacts = ArtifactStore(root)
        artifacts.write(_report("acme/cli", severity=Severity.NORMAL), ts=1000.0)
        artifacts.write(
            _report("acme/cli", severity=Severity.CRITICAL), ts=3000.0
        )
        artifacts.write(
            _report("acme/gateway", kind=ReportKind.EXPLAIN, severity=Severity.INFO),
            ts=2000.0,
        )
        ov = data.build_overview(_settings(artifact_root=root), store, artifacts)

        # recent: newest first across repos
        assert [c.ts for c in ov.recent_reports] == [3000, 2000, 1000]
        # supply-chain alerts: only critical/high
        assert [c.ts for c in ov.supply_chain_alerts] == [3000]
        # trend candidates: explain kind
        assert [c.ts for c in ov.trend_candidates] == [2000]
        # feed links are local & token-free
        assert {f.href for f in ov.feeds} == {"/feed/immediate", "/feed/digest"}
        for f in ov.feeds:
            assert "token" not in f.href.lower()
        assert ov.promotable_repos == frozenset()
        assert ov.to_dict()["promotable_repos"] == []


def test_build_overview_tracks_promotable_trend_candidates():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        store = StateStore(":memory:")
        artifacts = ArtifactStore(root)
        artifacts.write(
            _report("hot/repo", kind=ReportKind.EXPLAIN, severity=Severity.INFO),
            ts=2000.0,
        )
        ov = data.build_overview(_settings(artifact_root=root), store, artifacts)
        assert ov.promotable_repos == frozenset({"hot/repo"})
        assert ov.to_dict()["promotable_repos"] == ["hot/repo"]


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

        s = _settings(artifact_root=root)
        result = data.build_repo_runtime(s, store, artifacts, "acme/cli")
        assert result is not None
        runtime, cards = result
        assert runtime.in_watchlist is True
        assert runtime.promotable is False
        assert runtime.demotable is False
        assert runtime.baseline_rate == 2.5
        assert runtime.latest_stars == 180
        assert runtime.star_growth == 80
        assert runtime.cursor == "2026-01-01"
        assert len(cards) == 1

        store.promote_repo("hot/repo", source="mode-b")
        artifacts.write(_report("hot/repo"), ts=2000.0)
        promoted_result = data.build_repo_runtime(s, store, artifacts, "hot/repo")
        assert promoted_result is not None
        promoted_runtime, _ = promoted_result
        assert promoted_runtime.promotable is False
        assert promoted_runtime.demotable is True

        artifacts.write(_report("archived/repo"), ts=3000.0)
        archived_result = data.build_repo_runtime(s, store, artifacts, "archived/repo")
        assert archived_result is not None
        archived_runtime, _ = archived_result
        assert archived_runtime.promotable is True
        assert archived_runtime.demotable is False

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


# --- U3: Starlette app via TestClient ----------------------------------------


def _client(
    d,
    *,
    seed=True,
    raise_server_exceptions=True,
    write_store=None,
    admin_token=None,
):
    """Dashboard app + TestClient over in-memory store + tmp artifacts."""
    from starlette.testclient import TestClient

    from transmutary.dashboard.app import make_dashboard_app

    root = os.path.join(d, "artifacts")
    store = StateStore(":memory:")
    artifacts = ArtifactStore(root)
    if seed:
        artifacts.write(_report("acme/cli", severity=Severity.CRITICAL), ts=1000.0)
    settings = _settings(artifact_root=root)
    app = make_dashboard_app(
        settings,
        store,
        artifacts,
        write_store=write_store,
        admin_token=admin_token,
    )
    client = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=raise_server_exceptions,
    )
    return client, store, artifacts, settings


def _csrf(client):
    resp = client.get("/")
    assert resp.status_code == 200
    token = client.cookies.get("tmtry-csrf")
    assert token
    return token


def _login(client, *, token="secret-admin-token"):
    csrf = _csrf(client)
    resp = client.post(
        "/login",
        data={"admin_token": token, "csrf_token": csrf},
        headers={"origin": "http://localhost"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert client.cookies.get("tmtry-admin")
    return csrf


def test_index_lists_watchlist_with_source():
    with tempfile.TemporaryDirectory() as d:
        client, store, *_ = _client(d)
        store.promote_repo("hot/repo", source="mode-b")
        resp = client.get("/")
        assert resp.status_code == 200
        assert "acme/cli" in resp.text
        assert "config" in resp.text


def test_brand_renders_correct_chinese_name():
    # Regression: the brand zh name must be 嬗变, not a wrong codepoint (嘡变).
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/")
        assert "嬗变" in resp.text
        assert "嘡" not in resp.text


def test_report_page_renders_body():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/report/acme/cli/1000-diagnose.md")
        assert resp.status_code == 200
        assert "Report for acme/cli" in resp.text


def test_report_page_escapes_script_xss():
    with tempfile.TemporaryDirectory() as d:
        client, store, artifacts, settings = _client(d, seed=False)
        evil = Report(
            title="<script>alert('t')</script>",
            kind=ReportKind.DIAGNOSE,
            repo="acme/cli",
            severity=Severity.HIGH,
            body_md="<script>alert('b')</script>",
            created_at="2026-01-01T00:00:00Z",
        )
        artifacts.write(evil, ts=2000.0)
        resp = client.get("/report/acme/cli/2000-diagnose.md")
        assert resp.status_code == 200
        # R-D10: raw script tags never reach the page; escaped form does.
        assert "<script>alert(" not in resp.text
        assert "&lt;script&gt;" in resp.text


def test_report_page_blanks_javascript_source_href():
    with tempfile.TemporaryDirectory() as d:
        client, store, artifacts, settings = _client(d, seed=False)
        rpt = _report(
            "acme/cli",
            sources=(
                Source(source_id="ok", url="https://safe.example", fetched_at="2026"),
                Source(source_id="evil", url="javascript:alert(1)", fetched_at="2026"),
            ),
        )
        artifacts.write(rpt, ts=3000.0)
        resp = client.get("/report/acme/cli/3000-diagnose.md")
        assert resp.status_code == 200
        # R-D15: no javascript: href; the safe https one is linked.
        assert 'href="javascript:' not in resp.text
        assert 'href="https://safe.example"' in resp.text


def test_report_page_with_zh_body_shows_lang_toggle():
    """Bilingual report renders a language toggle and both bodies."""
    with tempfile.TemporaryDirectory() as d:
        client, store, artifacts, settings = _client(d, seed=False)
        rpt = Report(
            title="Bilingual report",
            kind=ReportKind.DIAGNOSE,
            repo="acme/cli",
            severity=Severity.HIGH,
            body_md="English body",
            body_md_zh="中文正文",
            created_at="2026-01-01T00:00:00Z",
        )
        artifacts.write(rpt, ts=4000.0)
        resp = client.get("/report/acme/cli/4000-diagnose.md")
        assert resp.status_code == 200
        assert "English body" in resp.text
        assert "中文正文" in resp.text
        assert "lang-toggle" in resp.text


def test_report_page_without_zh_body_no_toggle():
    """Monolingual report does not render a language toggle."""
    with tempfile.TemporaryDirectory() as d:
        client, store, artifacts, settings = _client(d, seed=False)
        rpt = _report("acme/cli")
        assert rpt.body_md_zh is None
        artifacts.write(rpt, ts=5000.0)
        resp = client.get("/report/acme/cli/5000-diagnose.md")
        assert resp.status_code == 200
        assert "lang-toggle" not in resp.text


def test_report_json_includes_body_zh():
    """JSON format includes body_zh from sidecar."""
    with tempfile.TemporaryDirectory() as d:
        client, store, artifacts, settings = _client(d, seed=False)
        rpt = Report(
            title="JSON bilingual",
            kind=ReportKind.DIAGNOSE,
            repo="acme/cli",
            severity=Severity.HIGH,
            body_md="EN",
            body_md_zh="ZH",
            created_at="2026-01-01T00:00:00Z",
        )
        artifacts.write(rpt, ts=6000.0)
        resp = client.get("/report/acme/cli/6000-diagnose.md?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["body_zh"] == "ZH"


def test_report_path_traversal_404():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        # R-D11: illegal filename → 404, never reads outside the dir.
        assert client.get("/report/acme/cli/..%2f..%2fetc%2fpasswd").status_code == 404
        assert client.get("/report/acme/cli/evil.txt").status_code == 404


def test_unknown_repo_404():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        assert client.get("/repo/no/repo").status_code == 404


def test_routes_are_get_only_except_promote_writes():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        from starlette.routing import Route

        for route in client.app.routes:
            if isinstance(route, Route):
                # R-D7: read-only — only GET (Starlette auto-adds HEAD).
                if route.path in {
                    "/promote",
                    "/demote",
                    "/login",
                    "/settings/repos",
                    "/settings/repos/remove",
                    "/settings/edges",
                    "/settings/edges/remove",
                    "/settings/trends",
                    "/settings/delivery",
                    "/settings/llm",
                    "/settings/llm/test",
                }:
                    assert route.methods <= {"GET", "POST", "HEAD"}
                else:
                    assert route.methods <= {"GET", "HEAD"}


def test_host_header_allowlist():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        # R-D14: foreign Host → 400 (DNS-rebind defense); localhost → ok.
        assert client.get("/", headers={"host": "evil.com"}).status_code == 400
        assert client.get("/", headers={"host": "127.0.0.1:8787"}).status_code == 200
        assert client.get("/", headers={"host": "localhost"}).status_code == 200


def test_security_headers_present():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/")
        # R-D18
        assert "default-src 'self'" in resp.headers["content-security-policy"]
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"


def test_internal_error_does_not_leak(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d, raise_server_exceptions=False)
        from transmutary.dashboard import app as app_mod

        def _boom(*a, **k):
            raise ValueError("/secret/path/to/state.sqlite3 leaked")

        monkeypatch.setattr(app_mod.data, "build_overview", _boom)
        resp = client.get("/")
        assert resp.status_code == 500
        # R-D17: generic body — no path / exception class / traceback.
        assert "/secret/path" not in resp.text
        assert "ValueError" not in resp.text
        assert "Traceback" not in resp.text


def test_feed_links_carry_no_token():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/")
        # R-D9: feed hrefs in the rendered HTML never embed a token.
        assert "/feed/immediate" in resp.text
        assert "token=" not in resp.text.lower()
        assert "bearer" not in resp.text.lower()


def test_healthz_ok():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        assert client.get("/healthz").status_code == 200


# --- promote UI U2: write routes --------------------------------------------


def test_promote_post_requires_write_store():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.post(
            "/promote",
            data={"repo": "hot/repo", "csrf_token": "unused"},
            headers={"origin": "http://localhost"},
        )
        assert resp.status_code == 404
        assert "read-only" in client.get("/").text


def test_resolve_write_store_localhost_opens_rw_store(tmp_path):
    from transmutary.dashboard.app import resolve_write_store

    settings = _settings(artifact_root=str(tmp_path), repos=("acme/cli",))
    settings = dataclasses.replace(
        settings,
        delivery=dataclasses.replace(
            settings.delivery, state_db_path=str(tmp_path / "state.sqlite")
        ),
    )
    store = resolve_write_store(settings, is_local=True, allow_public_writes=False)
    try:
        assert store is not None
        assert store.read_only is False
        store.promote_repo("hot/repo")
        assert store.is_promoted("hot/repo") is True
    finally:
        assert store is not None
        store.close()


def test_resolve_write_store_public_requires_explicit_gate(tmp_path, caplog):
    from transmutary.dashboard.app import resolve_write_store

    settings = _settings(artifact_root=str(tmp_path), repos=("acme/cli",))
    settings = dataclasses.replace(
        settings,
        delivery=dataclasses.replace(
            settings.delivery, state_db_path=str(tmp_path / "state.sqlite")
        ),
    )
    assert resolve_write_store(settings, is_local=False, allow_public_writes=False) is None

    store = resolve_write_store(settings, is_local=False, allow_public_writes=True)
    try:
        assert store is not None
        assert "PUBLIC write endpoints" in caplog.text
    finally:
        assert store is not None
        store.close()


def test_promote_post_writes_table_and_redirects():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store)
        assert "writes enabled" in client.get("/").text
        token = _csrf(client)
        resp = client.post(
            "/promote",
            data={"repo": "hot/repo", "csrf_token": token},
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert write_store.is_promoted("hot/repo") is True


def test_demote_post_removes_promoted_repo():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        write_store.promote_repo("hot/repo")
        client, *_ = _client(d, write_store=write_store)
        token = _csrf(client)
        resp = client.post(
            "/demote",
            data={"repo": "hot/repo", "csrf_token": token},
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert write_store.is_promoted("hot/repo") is False


@pytest.mark.parametrize(
    ("data", "headers"),
    [
        ({"repo": "hot/repo"}, {"origin": "http://localhost"}),
        ({"repo": "hot/repo", "csrf_token": "bad"}, {"origin": "http://localhost"}),
        ({"repo": "hot/repo", "csrf_token": "TOKEN"}, {"origin": "http://evil.com"}),
        ({"repo": "hot/repo", "csrf_token": "TOKEN"}, {}),
    ],
)
def test_promote_post_rejects_csrf_and_origin_failures(data, headers):
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store)
        token = _csrf(client)
        payload = {k: (token if v == "TOKEN" else v) for k, v in data.items()}
        resp = client.post("/promote", data=payload, headers=headers)
        assert resp.status_code == 403
        assert "Request rejected" in resp.text
        assert write_store.is_promoted("hot/repo") is False


@pytest.mark.parametrize("repo", ["hot", "javascript:alert(1)/x", "hot/repo extra"])
def test_promote_post_rejects_invalid_repo(repo):
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store)
        token = _csrf(client)
        resp = client.post(
            "/promote",
            data={"repo": repo, "csrf_token": token},
            headers={"origin": "http://localhost"},
        )
        assert resp.status_code == 400
        assert write_store.list_promoted() == []


def test_demote_post_rejects_non_promoted_repo():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store)
        token = _csrf(client)
        resp = client.post(
            "/demote",
            data={"repo": "acme/cli", "csrf_token": token},
            headers={"origin": "http://localhost"},
        )
        assert resp.status_code == 400
        assert "promoted" in resp.text or "晋升" in resp.text


def test_promote_confirm_page_contains_csrf_form_and_does_not_write():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store)
        token = _csrf(client)
        resp = client.get("/promote?repo=hot/repo")
        assert resp.status_code == 200
        assert 'action="/promote"' in resp.text
        assert 'name="repo" value="hot/repo"' in resp.text
        assert f'name="csrf_token" value="{token}"' in resp.text
        assert write_store.is_promoted("hot/repo") is False


def test_write_store_none_hides_confirm_routes_too():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        assert client.get("/promote?repo=hot/repo").status_code == 404
        assert client.get("/demote?repo=hot/repo").status_code == 404


def test_stylesheet_served_same_origin_css():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/static/dashboard.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]
        # sanity: the stylesheet actually carries our rules
        assert ".sev-critical" in resp.text


def test_index_links_stylesheet():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/")
        # base.html references the same-origin stylesheet (CSP default-src 'self' OK)
        assert '/static/dashboard.css?v=' in resp.text
        assert '/static/dashboard.js?v=' in resp.text


# --- admin settings U3-U5 ----------------------------------------------------


def test_settings_requires_admin_token_configured():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store)
        resp = client.get("/settings")
        assert resp.status_code == 503
        assert "TRANSMUTARY_ADMIN_TOKEN" in resp.text


def test_settings_redirects_to_login_when_unauthenticated():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        resp = client.get("/settings", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


def test_login_sets_http_only_admin_session_cookie_and_rejects_bad_token():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        csrf = _csrf(client)
        bad = client.post(
            "/login",
            data={"admin_token": "wrong", "csrf_token": csrf},
            headers={"origin": "http://localhost"},
        )
        assert bad.status_code == 403
        assert "secret-admin-token" not in bad.text
        assert "tmtry-admin" not in client.cookies

        good = client.post(
            "/login",
            data={"admin_token": "secret-admin-token", "csrf_token": csrf},
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert good.status_code == 303
        assert client.cookies.get("tmtry-admin")
        set_cookie = good.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie


def test_settings_page_renders_secret_status_without_values(monkeypatch):
    monkeypatch.setenv("TRANSMUTARY_GITHUB_TOKEN", "ghp_supersecretshouldnotrender000000")
    monkeypatch.setenv("TRANSMUTARY_SMTP_PASSWORD", "smtp-secret-pw")
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        _login(client)
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text
        assert "TRANSMUTARY_GITHUB_TOKEN" in resp.text
        assert "configured" in resp.text
        assert "ghp_supersecretshouldnotrender" not in resp.text
        assert "smtp-secret-pw" not in resp.text


def test_settings_page_uses_admin_control_plane_layout():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        write_store.add_admin_repo("admin/repo")
        write_store.add_admin_dependency_edge("admin/repo", "acme/cli")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        _login(client)
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert 'class="status-strip"' in resp.text
        assert 'class="settings-nav"' in resp.text
        assert 'class="settings-grid"' in resp.text
        for section in ("repos", "edges", "trends", "delivery", "runtime"):
            assert f'id="{section}"' in resp.text
        assert "admin/repo" in resp.text
        assert "Control plane" in resp.text


def test_settings_add_and_remove_admin_repo_requires_auth_and_csrf():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        resp = client.post(
            "/settings/repos",
            data={"repo": "admin/repo", "csrf_token": "bad"},
            headers={"origin": "http://localhost"},
        )
        assert resp.status_code == 403
        assert write_store.list_admin_repos() == []

        csrf = _login(client)
        resp = client.post(
            "/settings/repos",
            data={"repo": "admin/repo", "csrf_token": csrf},
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert write_store.list_admin_repos() == ["admin/repo"]

        csrf = client.cookies.get("tmtry-csrf")
        resp = client.post(
            "/settings/repos/remove",
            data={"repo": "admin/repo", "csrf_token": csrf},
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert write_store.list_admin_repos() == []


def test_settings_rejects_invalid_repo_without_mutating():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        csrf = _login(client)
        resp = client.post(
            "/settings/repos",
            data={"repo": "not a repo", "csrf_token": csrf},
            headers={"origin": "http://localhost"},
        )
        assert resp.status_code == 400
        assert write_store.list_admin_repos() == []


def test_settings_dependency_edge_validates_effective_repos():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        write_store.add_admin_repo("admin/repo")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        csrf = _login(client)
        bad = client.post(
            "/settings/edges",
            data={
                "from_repo": "admin/repo",
                "to_repo": "ghost/repo",
                "csrf_token": csrf,
            },
            headers={"origin": "http://localhost"},
        )
        assert bad.status_code == 400
        assert write_store.list_admin_dependency_edges() == []

        ok = client.post(
            "/settings/edges",
            data={
                "from_repo": "admin/repo",
                "to_repo": "acme/cli",
                "csrf_token": csrf,
            },
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert ok.status_code == 303
        assert [(e.from_repo, e.to_repo) for e in write_store.list_admin_dependency_edges()] == [
            ("admin/repo", "acme/cli")
        ]


def test_settings_trend_and_delivery_forms_update_store():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        csrf = _login(client)
        resp = client.post(
            "/settings/trends",
            data={
                "topics": "agent\nai\nagent",
                "keywords": "rag\nevals",
                "csrf_token": csrf,
            },
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert write_store.list_admin_trend_topics() == ["agent", "ai"]
        assert write_store.list_admin_trend_keywords() == ["evals", "rag"]

        resp = client.post(
            "/settings/delivery",
            data={
                "email_recipients": "a@example.com\nb@example.com",
                "digest_hour": "17",
                "csrf_token": csrf,
            },
            headers={"origin": "http://localhost"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        prefs = write_store.get_admin_delivery_preferences()
        assert prefs.email_recipients == ["a@example.com", "b@example.com"]
        assert prefs.digest_hour == 17


def test_settings_delivery_rejects_invalid_values():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        csrf = _login(client)
        bad_hour = client.post(
            "/settings/delivery",
            data={
                "email_recipients": "a@example.com",
                "digest_hour": "25",
                "csrf_token": csrf,
            },
            headers={"origin": "http://localhost"},
        )
        assert bad_hour.status_code == 400
        bad_email = client.post(
            "/settings/delivery",
            data={
                "email_recipients": "not-email",
                "digest_hour": "9",
                "csrf_token": csrf,
            },
            headers={"origin": "http://localhost"},
        )
        assert bad_email.status_code == 400
        assert write_store.get_admin_delivery_preferences().digest_hour is None


def test_settings_pages_have_no_inline_style():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        _login(client)
        resp = client.get("/settings")
        assert "style=" not in resp.text


def test_login_page_uses_admin_access_layout():
    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        resp = client.get("/login")
        assert resp.status_code == 200
        assert 'class="login-shell"' in resp.text
        assert 'class="login-card"' in resp.text
        assert "Console access" in resp.text
        assert "Secret boundary" in resp.text
        assert "secret-admin-token" not in resp.text


def test_readme_demo_gif_has_vhs_source_and_command():
    with open("README.md", encoding="utf-8") as f:
        readme = f.read()
    with open("assets/demo.tape", encoding="utf-8") as f:
        tape = f.read()
    assert '<img src="assets/demo.gif"' in readme
    assert "vhs assets/demo.tape" in readme
    assert "Output assets/demo.gif" in tape
    assert 'Type "transmutary-demo"' in tape


def test_missing_jinja_raises_with_hint(monkeypatch):
    from transmutary.dashboard import app as app_mod

    monkeypatch.setattr(app_mod, "_jinja2", None)
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        with pytest.raises(RuntimeError, match="transmutary\\[dashboard\\]"):
            app_mod.make_dashboard_app(
                _settings(artifact_root=root),
                StateStore(":memory:"),
                ArtifactStore(root),
            )


# --- U4: resolve_bind public opt-in gate (R-D16) -----------------------------


def test_resolve_bind_localhost_default():
    from transmutary.dashboard.app import resolve_bind

    assert resolve_bind("127.0.0.1", 8787, allow_public=False) == ("127.0.0.1", 8787)
    assert resolve_bind("localhost", 9000, allow_public=False) == ("localhost", 9000)


def test_resolve_bind_public_refused_without_flag():
    from transmutary.dashboard.app import resolve_bind

    # R-D16: non-localhost without --allow-public is a hard error, not a warning.
    with pytest.raises(SystemExit):
        resolve_bind("0.0.0.0", 8787, allow_public=False)


def test_resolve_bind_public_allowed_with_flag():
    from transmutary.dashboard.app import resolve_bind

    assert resolve_bind("0.0.0.0", 8787, allow_public=True) == ("0.0.0.0", 8787)


# --- U1: CSP nonce middleware (R-C1/R-C2) ------------------------------------


def test_csp_nonce_per_request_unique():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        c1 = client.get("/").headers["content-security-policy"]
        c2 = client.get("/").headers["content-security-policy"]
        import re

        n1 = re.search(r"'nonce-([^']+)'", c1)
        n2 = re.search(r"'nonce-([^']+)'", c2)
        assert n1 and n2
        # R-C2: per-request unique nonce.
        assert n1.group(1) != n2.group(1)


def test_csp_no_unsafe_directives():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        csp = client.get("/").headers["content-security-policy"]
        # R-C1: nonce is a precise allow, never a relaxation.
        assert "unsafe-inline" not in csp
        assert "unsafe-eval" not in csp
        assert "script-src 'self' 'nonce-" in csp
        assert "default-src 'self'" in csp
        assert "form-action 'self'" in csp


def test_csp_nonce_matches_request_state():
    from transmutary.dashboard.app import _csp_with_nonce

    # fail-closed: empty nonce → no-nonce CSP (inline script blocked, not allowed).
    no_nonce = _csp_with_nonce("")
    assert "nonce-" not in no_nonce
    assert no_nonce == (
        "default-src 'self'; script-src 'self'; style-src 'self'; form-action 'self'"
    )
    # with nonce → precise allow.
    withn = _csp_with_nonce("abc123")
    assert "'nonce-abc123'" in withn
    assert "unsafe-inline" not in withn


def test_csp_500_uses_no_nonce_policy(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d, raise_server_exceptions=False)
        from transmutary.dashboard import app as app_mod

        monkeypatch.setattr(
            app_mod.data, "build_overview", lambda *a, **k: (_ for _ in ()).throw(ValueError("x"))
        )
        resp = client.get("/")
        assert resp.status_code == 500
        csp = resp.headers["content-security-policy"]
        # 500 page has no inline script → no-nonce CSP, still locked down.
        assert "nonce-" not in csp
        assert "unsafe-inline" not in csp
        assert "default-src 'self'" in csp
        assert "form-action 'self'" in csp


# --- promote UI U1: CSRF double-submit primitives ----------------------------


def test_csrf_token_issue_and_verify():
    from transmutary.dashboard.csrf import issue_token, verify_token

    token = issue_token()
    other = issue_token()
    assert token != other
    assert len(token) >= 32
    assert verify_token(token, token) is True
    assert verify_token(token, other) is False
    assert verify_token("", token) is False
    assert verify_token(token, None) is False
    assert verify_token("a" * 43, "b" * 43) is False


def test_check_origin_accepts_allowed_origin_and_referer():
    from starlette.requests import Request

    from transmutary.dashboard.csrf import check_origin

    allowed = frozenset({"localhost", "127.0.0.1", "[::1]"})

    def req(headers):
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ]})

    assert check_origin(req({"origin": "http://localhost"}), allowed) is True
    assert check_origin(req({"origin": "http://127.0.0.1:8787"}), allowed) is True
    assert check_origin(req({"origin": "http://[::1]:8787"}), allowed) is True
    assert check_origin(req({"referer": "http://localhost/promote"}), allowed) is True


def test_check_origin_rejects_cross_site_null_and_missing_headers():
    from starlette.requests import Request

    from transmutary.dashboard.csrf import check_origin

    allowed = frozenset({"localhost"})

    def req(headers):
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ]})

    assert check_origin(req({"origin": "http://evil.com"}), allowed) is False
    assert check_origin(req({"origin": "null", "referer": "http://localhost/"}), allowed) is True
    assert check_origin(req({"origin": "null"}), allowed) is False
    assert check_origin(req({}), allowed) is False


def test_csrf_middleware_sets_strict_httponly_cookie_and_reuses_it():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d, write_store=StateStore(":memory:"))
        first = client.get("/")
        cookie = first.cookies.get("tmtry-csrf")
        assert cookie
        set_cookie = first.headers["set-cookie"]
        assert "tmtry-csrf=" in set_cookie
        assert "SameSite=strict" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/" in set_cookie

        second = client.get("/")
        assert client.cookies.get("tmtry-csrf") == cookie
        assert "set-cookie" not in second.headers


def test_csrf_middleware_disabled_when_dashboard_is_read_only():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/")
        assert "tmtry-csrf" not in resp.cookies
        assert "set-cookie" not in resp.headers


# --- review fixes: IPv6 host, 500 headers, sidecar trust, _safe_url, read-only ---


def test_host_only_parses_ipv6_and_ipv4():
    from transmutary.dashboard.app import _host_only

    assert _host_only("[::1]:8787") == "[::1]"
    assert _host_only("[::1]") == "[::1]"
    assert _host_only("::1") == "::1"
    assert _host_only("127.0.0.1:8787") == "127.0.0.1"
    assert _host_only("127.0.0.1") == "127.0.0.1"
    assert _host_only("localhost:8787") == "localhost"
    assert _host_only("") == ""


def test_host_allowlist_accepts_ipv6_localhost():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        # R-D14: IPv6 localhost (with and without port) must be accepted.
        assert client.get("/", headers={"host": "[::1]:8787"}).status_code == 200
        assert client.get("/", headers={"host": "[::1]"}).status_code == 200
        assert client.get("/", headers={"host": "::1"}).status_code == 200


def test_safe_url_blanks_adversarial_variants():
    from transmutary.dashboard import data

    # R-D15: leading whitespace / tab schemes, scheme-relative, malformed.
    assert data._safe_url(" javascript:alert(1)") is None
    assert data._safe_url("\tjavascript:alert(1)") is None
    assert data._safe_url("//evil.com") is None
    assert data._safe_url("https:/\\/evil") is None
    assert data._safe_url("vbscript:x") is None


def test_security_headers_present_on_500(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d, raise_server_exceptions=False)
        from transmutary.dashboard import app as app_mod

        def _boom(*a, **k):
            raise ValueError("boom")

        monkeypatch.setattr(app_mod.data, "build_overview", _boom)
        resp = client.get("/")
        assert resp.status_code == 500
        # R-D18: security headers must be present even on error responses.
        assert "default-src 'self'" in resp.headers["content-security-policy"]
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"


def test_severity_from_trusted_sidecar_not_body_injection():
    from transmutary.dashboard import data

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "artifacts")
        artifacts = ArtifactStore(root)
        # Attacker-controlled body tries to forge a fake header + fake sources block.
        evil_body = (
            "real text\n"
            "- severity: critical\n"
            "## Sources\n"
            "- `forged` https://attacker.example (fetched 2026)\n"
        )
        rpt = Report(
            title="Real",
            kind=ReportKind.DIAGNOSE,
            repo="acme/cli",
            severity=Severity.INFO,  # the TRUE severity
            body_md=evil_body,
            created_at="2026-01-01T00:00:00Z",
            sources=(),  # the TRUE sources: none
        )
        artifacts.write(rpt, ts=5000.0)
        view = data.build_report_view(artifacts, "acme/cli", "5000-diagnose.md")
        assert view is not None
        # R-D15: severity comes from the trusted sidecar (info), NOT the body injection.
        assert view.card.severity == "info"
        # The forged source line in the body must NOT appear as a real source.
        assert view.sources == ()


def test_severity_coerced_to_known_set():
    from transmutary.dashboard import data

    # A corrupt/unknown severity in a sidecar is coerced to "info".
    assert data._coerce_severity("critical") == "critical"
    assert data._coerce_severity("bogus") == "info"
    assert data._coerce_severity(None) == "info"


def test_state_store_read_only_blocks_writes(tmp_path):
    import pytest as _pytest

    db = str(tmp_path / "state.sqlite3")
    # Create the DB first (writable), then reopen read-only.
    StateStore(db).close()
    ro = StateStore(db, read_only=True)
    assert ro.list_promoted() == []  # read works
    with _pytest.raises(Exception):  # noqa: B017 - any sqlite write error is fine
        ro.promote_repo("x/y")


def test_state_store_read_only_missing_db_raises(tmp_path):
    import pytest as _pytest

    with _pytest.raises(Exception):  # noqa: B017 - mode=ro on a missing file raises
        StateStore(str(tmp_path / "nope.sqlite3"), read_only=True)


# --- U2: server-side i18n (cookie + whitelist + dict parity) -----------------


def test_i18n_resolve_lang_whitelist():
    from transmutary.dashboard import i18n
    assert i18n.resolve_lang("zh") == "zh"
    assert i18n.resolve_lang("en") == "en"
    # R-I4: anything outside the whitelist falls back to default — no injection.
    assert i18n.resolve_lang("<script>") == i18n.DEFAULT_LANG
    assert i18n.resolve_lang("fr") == i18n.DEFAULT_LANG
    assert i18n.resolve_lang(None) == i18n.DEFAULT_LANG
    assert i18n.resolve_lang("zh" * 500) == i18n.DEFAULT_LANG


def test_i18n_dict_key_parity():
    from transmutary.dashboard import i18n
    # en and zh must carry identical keys (no missing translations).
    assert set(i18n.MESSAGES["en"]) == set(i18n.MESSAGES["zh"])


def test_pages_embed_full_i18n_dictionary_for_client_switching():
    from transmutary.dashboard import i18n

    with tempfile.TemporaryDirectory() as d:
        write_store = StateStore(":memory:")
        client, *_ = _client(d, write_store=write_store, admin_token="secret-admin-token")
        resp = client.get("/login")
        assert resp.status_code == 200
        assert '<script type="application/json" id="i18n-dict">' in resp.text
        for key in i18n.MESSAGES["en"]:
            assert f'"{key}"' in resp.text
        assert "控制台访问" in resp.text
        assert "Admin login" in resp.text
        assert ".innerHTML" not in resp.text


def test_i18n_cookie_drives_first_paint_language():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        zh = client.get("/", headers={"cookie": "tmtry-lang=zh"})
        assert zh.status_code == 200
        assert "总览" in zh.text  # zh chrome rendered server-side
        assert 'lang="zh-CN"' in zh.text
        en = client.get("/", headers={"cookie": "tmtry-lang=en"})
        assert "Overview" in en.text
        assert 'lang="en"' in en.text


def test_i18n_bad_cookie_falls_back_not_injected():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/", headers={"cookie": "tmtry-lang=%3Cscript%3Ealert(1)%3C/script%3E"})
        assert resp.status_code == 200
        assert "<script>alert(1)" not in resp.text  # never reaches the markup
        assert 'lang="en"' in resp.text  # fell back to default


def test_i18n_data_not_translated():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/", headers={"cookie": "tmtry-lang=zh"})
        # repo names / severity stay original even in zh.
        assert "acme/cli" in resp.text


# --- U4/U5: nonce FOUC script, zero inline style ----------------------------


def test_head_fouc_script_carries_nonce():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/")
        import re
        csp = resp.headers["content-security-policy"]
        m = re.search(r"'nonce-([^']+)'", csp)
        assert m
        nonce = m.group(1)
        # the inline FOUC script must carry the SAME nonce (R-T2/R-C2).
        assert f'nonce="{nonce}"' in resp.text


def test_templates_have_no_inline_style():
    with tempfile.TemporaryDirectory() as d:
        client, store, arts, settings = _client(d)
        store.promote_repo("hot/repo", source="mode-b")
        for path in ("/", "/repo/acme/cli"):
            resp = client.get(path)
            # style-src 'self' would silently block inline style= attributes.
            assert "style=" not in resp.text, f"inline style in {path}"


def test_js_served_same_origin():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/static/dashboard.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        # i18n DOM writes use textContent, never innerHTML (P0). Assert no actual
        # property write — `.innerHTML` / insertAdjacentHTML — not a bare mention.
        assert ".innerHTML" not in resp.text
        assert "insertAdjacentHTML" not in resp.text
        assert "textContent" in resp.text


# --- U6: JSON content negotiation + /llms.txt -------------------------------


def test_json_via_accept_header():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/", headers={"accept": "application/json"})
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]
        body = resp.json()
        assert "watchlist" in body and "supply_chain_alerts" in body


def test_json_via_query_param():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/?format=json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]


def test_json_excludes_credentials():
    from transmutary.deliver.server import hash_token
    with tempfile.TemporaryDirectory() as d:
        client, store, arts, settings = _client(d)
        store.add_subscriber_token(hash_token("super-secret-xyz"), "alice")
        resp = client.get("/", headers={"accept": "application/json"})
        blob = resp.text.lower()
        # R-S2: same exclusion guarantee as the HTML path.
        assert "super-secret-xyz" not in blob
        assert "alice" not in blob
        assert hash_token("super-secret-xyz") not in blob


def test_json_repo_key_allowlist():
    with tempfile.TemporaryDirectory() as d:
        client, store, arts, settings = _client(d)
        from transmutary.report.schema import Severity
        arts.write(_report("acme/cli", severity=Severity.HIGH), ts=1000.0)
        resp = client.get("/repo/acme/cli", headers={"accept": "application/json"})
        assert resp.status_code == 200
        runtime = resp.json()["runtime"]
        # P1: explicit allow-list — exactly these keys, no asdict-style spill.
        assert set(runtime.keys()) == {
            "repo", "in_watchlist", "source", "baseline_rate",
            "latest_stars", "star_growth", "cursor", "promotable", "demotable",
        }


def test_json_still_host_guarded():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        # JSON path still goes through the Host allow-list.
        resp = client.get("/", headers={"accept": "application/json", "host": "evil.com"})
        assert resp.status_code == 400


def test_llms_txt_no_private_data():
    with tempfile.TemporaryDirectory() as d:
        client, store, arts, settings = _client(d)
        store.promote_repo("secret-org/private-repo", source="mode-b")
        resp = client.get("/llms.txt")
        assert resp.status_code == 200
        assert "markdown" in resp.headers["content-type"]
        # describes endpoints, never leaks the real watched repos.
        assert "secret-org/private-repo" not in resp.text
        assert "/repo/{owner}/{repo}" in resp.text
        assert "application/json" in resp.text


def test_llms_txt_host_guarded():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        resp = client.get("/llms.txt", headers={"host": "evil.com"})
        assert resp.status_code == 400


def test_json_report_marks_external_trust():
    with tempfile.TemporaryDirectory() as d:
        client, store, arts, settings = _client(d)
        from transmutary.report.schema import Severity
        arts.write(_report("acme/cli", severity=Severity.HIGH), ts=1000.0)
        resp = client.get(
            "/report/acme/cli/1000-diagnose.md", headers={"accept": "application/json"}
        )
        assert resp.status_code == 200
        assert resp.json()["_content_trust"] == "external"
