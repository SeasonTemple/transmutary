"""Email HTML rendering tests (R5/R8/R9) + multipart message build."""

from __future__ import annotations

from transmutary.deliver.email import _build_message
from transmutary.deliver.render_email import render_email_html, render_email_text
from transmutary.report.schema import Report, ReportKind, Severity, Source


def _report(*, body_zh="中文正文：疑似上游发布。", sources=True):
    return Report(
        kind=ReportKind.DIAGNOSE,
        repo="acme/cli",
        title="gateway 504 outage",
        body_md="# Root cause\n\n- upstream **release**\n- [advisory](https://x.test)",
        severity=Severity.HIGH,
        created_at="2026-06-04T10:00:00+00:00",
        body_md_zh=body_zh,
        sources=[Source(source_id="GHSA-x", url="https://github.com/advisories/GHSA-x",
                        fetched_at="2026-06-04T10:00:00+00:00")] if sources else [],
    )


def test_html_renders_markdown_body():
    html = render_email_html(_report())
    assert "<h1>Root cause</h1>" in html
    assert "<strong>release</strong>" in html
    assert '<a href="https://x.test">advisory</a>' in html


def test_html_has_bilingual_sections_with_lang():
    html = render_email_html(_report())
    assert 'lang="en"' in html
    assert 'lang="zh-CN"' in html
    assert "中文正文" in html


def test_html_severity_is_text_not_color_only():
    html = render_email_html(_report())
    assert "HIGH" in html  # severity stated in words (R9)


def test_html_escapes_untrusted_script():
    r = _report()
    r2 = Report(kind=r.kind, repo=r.repo, title="<script>alert(1)</script>",
                body_md="<script>evil()</script>", severity=r.severity,
                created_at=r.created_at, sources=[])
    html = render_email_html(r2)
    assert "<script>alert(1)" not in html  # title escaped
    assert "<script>evil()" not in html    # body raw-HTML escaped by renderer


def test_html_cjk_font_stack_present():
    html = render_email_html(_report())
    assert "PingFang SC" in html or "Noto Sans CJK" in html


def test_text_fallback_has_both_languages_and_sources():
    txt = render_email_text(_report())
    assert "Root cause" in txt
    assert "中文正文" in txt
    assert "GHSA-x" in txt


def test_build_message_is_multipart_alternative():
    msg = _build_message(_report(), sender="a@b.test", recipients=["c@d.test"])
    assert msg.get_content_type() == "multipart/alternative"
    types = [p.get_content_type() for p in msg.iter_parts()]
    assert "text/plain" in types
    assert "text/html" in types


def test_monolingual_report_has_no_zh_section():
    html = render_email_html(_report(body_zh=None))
    assert 'lang="zh-CN"' not in html
