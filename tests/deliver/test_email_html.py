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


def test_html_default_is_english_only():
    # Single-language push: default (en) renders English, NOT the Chinese body.
    html = render_email_html(_report())
    assert 'lang="en"' in html
    assert 'lang="zh-CN"' not in html
    assert "中文正文" not in html


def test_html_zh_renders_chinese_only():
    html = render_email_html(_report(), lang="zh")
    assert 'lang="zh-CN"' in html
    assert "中文正文" in html
    assert "Root cause" not in html  # English body absent


def test_html_zh_falls_back_to_english_when_no_translation():
    html = render_email_html(_report(body_zh=None), lang="zh")
    assert "Root cause" in html  # no zh body → English fallback, never blank
    assert 'lang="en"' in html


def test_html_timestamp_is_human_readable_not_raw_iso():
    html = render_email_html(_report())
    assert "2026-06-04 10:00 UTC" in html
    assert "2026-06-04T10:00:00+00:00" not in html


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


def test_text_fallback_single_language_and_sources():
    # Default (en): English body + sources, NOT the Chinese body.
    txt = render_email_text(_report())
    assert "Root cause" in txt
    assert "中文正文" not in txt
    assert "GHSA-x" in txt
    # source timestamp formatted, not a raw float/ISO
    assert "2026-06-04 10:00 UTC" in txt


def test_text_fallback_zh_renders_chinese():
    txt = render_email_text(_report(), lang="zh")
    assert "中文正文" in txt
    assert "Root cause" not in txt


def test_build_message_is_multipart_alternative():
    msg = _build_message(_report(), sender="a@b.test", recipients=["c@d.test"])
    assert msg.get_content_type() == "multipart/alternative"
    types = [p.get_content_type() for p in msg.iter_parts()]
    assert "text/plain" in types
    assert "text/html" in types


def test_monolingual_report_has_no_zh_section():
    html = render_email_html(_report(body_zh=None))
    assert 'lang="zh-CN"' not in html
