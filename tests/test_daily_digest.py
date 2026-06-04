"""Integration test for run_daily_digest orchestration (R10)."""

from __future__ import annotations

import tempfile

import httpx

from transmutary.config import Credentials, Delivery, Settings, TrendScope, Watchlist
from transmutary.pipeline import build_runtime, run_daily_digest
from transmutary.report.schema import Report, ReportKind, Severity, Source
from transmutary.store.state import StateStore


def _report(repo, title, severity):
    return Report(
        kind=ReportKind.DIAGNOSE, repo=repo, title=title,
        body_md=f"# {title}\n\nbody", severity=severity,
        created_at="2026-06-04T10:00:00+00:00", body_md_zh="中文",
        sources=[Source(source_id="s", url="https://x.test",
                        fetched_at="2026-06-04T10:00:00+00:00")],
    )


class _RecordingEmail:
    def __init__(self):
        self.sent = []

    def __call__(self):  # smtp_factory seam
        return self

    def starttls(self): pass
    def login(self, u, p): pass
    def send_message(self, msg): self.sent.append(msg)
    def quit(self): pass


def _settings(tmp, *, recipients=(), smtp_host=None):
    return Settings(
        watchlist=Watchlist(repos=[], dependency_edges=[]),
        trend_scope=TrendScope(topics=["ai"], keywords=[]),
        delivery=Delivery(
            state_db_path=":memory:",
            artifact_root=tmp,
            token_max_age_days=90,
            digest_hour=9,
            email_recipients=list(recipients),
            smtp_host=smtp_host,
            feed_dir=tmp + "/_feed",
        ),
    )


def _creds():
    return Credentials(
        github_token="ghp_x", smtp_user="a@b.test", smtp_password="pw",
        rss_token="rss", llm_api_key="",
    )


def _runtime(tmp, *, recipients=(), smtp_host=None):
    rec = _RecordingEmail()
    settings = _settings(tmp, recipients=recipients, smtp_host=smtp_host)
    rt = build_runtime(
        settings, _creds(), store=StateStore(":memory:"),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    rt.outbound.smtp_factory = rec
    return rt, rt.artifacts, rec


def test_digest_writes_html_and_rss():
    with tempfile.TemporaryDirectory() as tmp:
        rt, artifacts, _ = _runtime(tmp)
        artifacts.write(_report("a/b", "High one", Severity.HIGH), ts=10_000)
        artifacts.write(_report("c/d", "Info one", Severity.INFO), ts=10_001)
        res = run_daily_digest(rt, now_ts=10_500)
        assert res.report_count == 2
        assert res.html_path and res.html_path.endswith(".html")
        assert res.rss_path and res.rss_path.endswith("digest.atom.xml")
        with open(res.html_path, encoding="utf-8") as fh:
            html = fh.read()
        assert "High one" in html and "Info one" in html


def test_digest_empty_window_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        rt, artifacts, rec = _runtime(tmp, recipients=["x@y.test"], smtp_host="smtp.test")
        artifacts.write(_report("a/b", "old", Severity.INFO), ts=1)
        res = run_daily_digest(rt, now_ts=10_000_000)
        assert res.skipped_empty is True
        assert res.report_count == 0
        assert res.email_sent is False
        assert rec.sent == []  # no email on empty window


def test_digest_sends_email_when_configured():
    with tempfile.TemporaryDirectory() as tmp:
        rt, artifacts, rec = _runtime(tmp, recipients=["x@y.test"], smtp_host="smtp.test")
        artifacts.write(_report("a/b", "Alert", Severity.CRITICAL), ts=10_000)
        res = run_daily_digest(rt, now_ts=10_500)
        assert res.email_sent is True
        assert len(rec.sent) == 1
        msg = rec.sent[0]
        assert msg.get_content_type() == "multipart/alternative"
        assert "Daily Digest" in msg["Subject"]


def test_digest_no_email_without_recipients():
    with tempfile.TemporaryDirectory() as tmp:
        rt, artifacts, rec = _runtime(tmp, recipients=())
        artifacts.write(_report("a/b", "x", Severity.HIGH), ts=10_000)
        res = run_daily_digest(rt, now_ts=10_500)
        assert res.email_sent is False
        assert res.html_path is not None  # artifact still written
