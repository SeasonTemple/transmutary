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


# --- U3: Starlette app via TestClient ----------------------------------------


def _client(d, *, seed=True, raise_server_exceptions=True):
    """Dashboard app + TestClient over in-memory store + tmp artifacts."""
    from starlette.testclient import TestClient

    from transmutary.dashboard.app import make_dashboard_app

    root = os.path.join(d, "artifacts")
    store = StateStore(":memory:")
    artifacts = ArtifactStore(root)
    if seed:
        artifacts.write(_report("acme/cli", severity=Severity.CRITICAL), ts=1000.0)
    settings = _settings(artifact_root=root)
    app = make_dashboard_app(settings, store, artifacts)
    client = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=raise_server_exceptions,
    )
    return client, store, artifacts, settings


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


def test_routes_are_get_only():
    with tempfile.TemporaryDirectory() as d:
        client, *_ = _client(d)
        from starlette.routing import Route

        for route in client.app.routes:
            if isinstance(route, Route):
                # R-D7: read-only — only GET (Starlette auto-adds HEAD).
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
        assert '/static/dashboard.css' in resp.text


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


def test_csp_nonce_matches_request_state():
    from transmutary.dashboard.app import _csp_with_nonce

    # fail-closed: empty nonce → no-nonce CSP (inline script blocked, not allowed).
    no_nonce = _csp_with_nonce("")
    assert "nonce-" not in no_nonce
    assert no_nonce == "default-src 'self'; script-src 'self'; style-src 'self'"
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
            "latest_stars", "star_growth", "cursor",
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
