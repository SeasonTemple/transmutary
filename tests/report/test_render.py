"""Tests for the markdown renderer (R2/R3/R4 — XSS-safe rendering)."""

import pytest

from transmutary.report.render import fmt_timestamp, localized_body, render_markdown
from transmutary.report.schema import Report, ReportKind, Severity


def _report(body_zh=None):
    return Report(
        kind=ReportKind.DIAGNOSE, repo="a/b", title="T",
        body_md="english body", severity=Severity.HIGH,
        created_at="2026-06-04T10:00:00+00:00", body_md_zh=body_zh,
    )


def test_localized_body_zh_uses_translation():
    body, lang = localized_body(_report(body_zh="中文正文"), "zh")
    assert body == "中文正文" and lang == "zh-CN"


def test_localized_body_zh_falls_back_to_english():
    body, lang = localized_body(_report(body_zh=None), "zh")
    assert body == "english body" and lang == "en"  # never blank


def test_localized_body_default_english():
    body, lang = localized_body(_report(body_zh="中文正文"), "en")
    assert body == "english body" and lang == "en"


@pytest.mark.parametrize("value,expected", [
    ("2026-06-04T10:00:00+00:00", "2026-06-04 10:00 UTC"),  # tz-aware offset
    ("2026-06-04T10:00:00Z", "2026-06-04 10:00 UTC"),       # Z suffix
    (1749031200.0, "2025-06-04 10:00 UTC"),                 # epoch float
])
def test_fmt_timestamp_formats(value, expected):
    assert fmt_timestamp(value) == expected


def test_fmt_timestamp_converts_offset_to_utc():
    # +02:00 → UTC shifts back 2h
    assert fmt_timestamp("2026-06-04T12:00:00+02:00") == "2026-06-04 10:00 UTC"


@pytest.mark.parametrize("bad", ["not-a-date", "", 1e308, None])
def test_fmt_timestamp_never_raises(bad):
    # malformed/None/overflow → returns str(value), never raises (delivery render
    # must not crash on a bad timestamp).
    assert fmt_timestamp(bad) == str(bad)


def test_renders_headings_lists_links():
    out = render_markdown("# Title\n\n- a\n- b\n\n[ok](https://x.test)")
    assert "<h1>Title</h1>" in out
    assert "<ul>" in out and "<li>a</li>" in out
    assert '<a href="https://x.test">ok</a>' in out


def test_renders_emphasis_and_code():
    out = render_markdown("**bold** and `code`")
    assert "<strong>bold</strong>" in out
    assert "<code>code</code>" in out


def test_raw_script_is_escaped_not_executed():
    out = render_markdown("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_img_onerror_is_escaped():
    out = render_markdown("<img src=x onerror=alert(1)>")
    assert "<img" not in out
    assert "onerror" not in out or "&lt;img" in out


def test_javascript_link_rejected():
    out = render_markdown("[click](javascript:alert(1))")
    assert 'href="javascript' not in out.lower()


def test_vbscript_and_data_html_links_rejected():
    assert 'href="vbscript' not in render_markdown("[a](vbscript:x)").lower()
    assert 'href="data:text/html' not in render_markdown("[b](data:text/html,<script>)").lower()


def test_javascript_link_case_and_whitespace_variants_rejected():
    for src in ("[a](JaVaScRiPt:x)", "[b](  javascript:x)", "[c](java\tscript:x)"):
        assert 'href="javascript' not in render_markdown(src).lower()


def test_empty_and_none_return_empty():
    assert render_markdown("") == ""
    assert render_markdown(None) == ""


def test_chinese_markdown_renders():
    out = render_markdown("## 中文标题\n\n- 项目一\n- 项目二")
    assert "<h2>中文标题</h2>" in out
    assert "<li>项目一</li>" in out


def test_safe_link_schemes_preserved():
    assert '<a href="https://x.test">' in render_markdown("[a](https://x.test)")
    assert '<a href="http://x.test">' in render_markdown("[a](http://x.test)")
    assert 'href="mailto:a@b.test"' in render_markdown("[a](mailto:a@b.test)")
