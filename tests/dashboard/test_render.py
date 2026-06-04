"""Tests for the markdown renderer (R2/R3/R4 — XSS-safe rendering)."""

from transmutary.dashboard.render import render_markdown


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
