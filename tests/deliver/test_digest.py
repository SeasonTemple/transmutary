"""Daily aggregate digest tests (R10/R8/R9)."""

from __future__ import annotations

import tempfile

from transmutary.deliver.digest import (
    WINDOW_SECONDS,
    collect_digest_reports,
    render_digest_html,
    render_digest_text,
)
from transmutary.report.schema import Report, ReportKind, Severity, Source
from transmutary.store.artifacts import ArtifactStore


def _report(repo, title, severity, *, body_zh=None):
    return Report(
        kind=ReportKind.DIAGNOSE,
        repo=repo,
        title=title,
        body_md=f"# {title}\n\nbody for {title}",
        severity=severity,
        created_at="2026-06-04T10:00:00+00:00",
        body_md_zh=body_zh,
        sources=[Source(source_id="s1", url="https://x.test",
                        fetched_at="2026-06-04T10:00:00+00:00")],
    )


def test_collect_window_filters_by_ts():
    with tempfile.TemporaryDirectory() as d:
        store = ArtifactStore(d)
        store.write(_report("a/b", "fresh", Severity.HIGH), ts=10_000)
        store.write(_report("a/b", "stale", Severity.INFO), ts=100)
        reports = collect_digest_reports(store, now_ts=10_000 + WINDOW_SECONDS - 1)
        titles = [r.title for r in reports]
        assert "fresh" in titles
        assert "stale" not in titles  # outside window


def test_collect_boundary_inclusive():
    with tempfile.TemporaryDirectory() as d:
        store = ArtifactStore(d)
        store.write(_report("a/b", "edge", Severity.INFO), ts=5_000)
        # now - window == 5000 → ts >= cutoff includes it
        reports = collect_digest_reports(store, now_ts=5_000 + WINDOW_SECONDS)
        assert any(r.title == "edge" for r in reports)


def test_collect_sorts_urgent_first():
    with tempfile.TemporaryDirectory() as d:
        store = ArtifactStore(d)
        store.write(_report("a/b", "info1", Severity.INFO), ts=10_000)
        store.write(_report("a/b", "crit1", Severity.CRITICAL), ts=10_001)
        reports = collect_digest_reports(store, now_ts=10_001)
        assert reports[0].severity is Severity.CRITICAL


def test_collect_multi_repo():
    with tempfile.TemporaryDirectory() as d:
        store = ArtifactStore(d)
        store.write(_report("a/one", "r1", Severity.HIGH), ts=10_000)
        store.write(_report("b/two", "r2", Severity.HIGH), ts=10_000)
        reports = collect_digest_reports(store, now_ts=10_500)
        repos = {r.repo for r in reports}
        assert repos == {"a/one", "b/two"}


def test_collect_empty_window():
    with tempfile.TemporaryDirectory() as d:
        store = ArtifactStore(d)
        store.write(_report("a/b", "old", Severity.INFO), ts=100)
        assert collect_digest_reports(store, now_ts=100 + WINDOW_SECONDS + 1) == []


def test_html_renders_reports_grouped():
    reports = [
        _report("a/b", "Critical issue", Severity.CRITICAL),
        _report("c/d", "Info note", Severity.INFO, body_zh="中文摘要"),
    ]
    html = render_digest_html(reports, date_label="2026-06-04")
    assert "Daily Digest 2026-06-04" in html
    assert "Critical issue" in html and "Info note" in html
    assert "2 report(s)" in html and "1 high-risk" in html
    assert 'lang="zh-CN"' in html and "中文摘要" in html


def test_html_escapes_untrusted_title():
    r = _report("a/b", "<script>alert(1)</script>", Severity.HIGH)
    html = render_digest_html([r], date_label="2026-06-04")
    assert "<script>alert(1)" not in html


def test_html_empty_window_message():
    html = render_digest_html([], date_label="2026-06-04")
    assert "No reports" in html


def test_html_cjk_font_stack():
    html = render_digest_html([_report("a/b", "x", Severity.INFO)], date_label="2026-06-04")
    assert "PingFang SC" in html or "Noto Sans CJK" in html


def test_text_fallback_lists_reports():
    reports = [_report("a/b", "T1", Severity.HIGH, body_zh="中文")]
    txt = render_digest_text(reports, date_label="2026-06-04")
    assert "Daily Digest 2026-06-04" in txt
    assert "[HIGH] a/b — T1" in txt
    assert "中文" in txt
