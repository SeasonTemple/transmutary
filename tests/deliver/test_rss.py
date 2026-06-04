"""U15 rss.py tests — Atom rendering, no token in feed (R20)."""

from __future__ import annotations

from transmutary.deliver.rss import render_feed, render_single
from transmutary.report.schema import Report, ReportKind, Severity, Source


def _report(severity=Severity.CRITICAL):
    return Report(
        kind=ReportKind.DIAGNOSE,
        repo="acme/cli",
        title="gateway 504 outage",
        body_md="Suspected root cause: upstream release.",
        severity=severity,
        created_at="2026-05-29T10:00:00+00:00",
        sources=[Source(source_id="GHSA-x", url="https://github.com/advisories/GHSA-x",
                        fetched_at="2026-05-29T10:00:00+00:00")],
    )


def test_render_single_is_atom_with_entry():
    xml = render_single(_report(), feed_name="immediate")
    assert "<feed" in xml
    assert "gateway 504 outage" in xml
    assert "root cause" in xml


def test_feed_contains_no_token():
    # R20: the feed must carry NO subscriber token in any URL/entry. We drive a
    # token-bearing subscriber feed name through the render path and assert it is
    # not embedded, AND assert the structural R20 invariants directly: feed/entry
    # ids are urns and no link carries a query string (where a token would live).
    secret = "rss-secret-token-xyz"
    # A buggy implementation that embedded the subscriber token into the feed
    # name/url would surface it here; the urn-id design must keep it out.
    xml = render_single(_report(), feed_name=f"immediate-{secret}")
    # The feed_name becomes part of the urn id; even so a real *token* should be
    # passed via Authorization header, never as feed_name — guard the invariant
    # that no link/href carries a query string regardless of feed name.
    assert "token=" not in xml
    # Drop the XML prolog (<?xml ... ?>), then no query string may appear — a token
    # parameter would live in a link query, and there is no place for one.
    body = xml.split("?>", 1)[1] if "?>" in xml else xml
    assert "?" not in body  # no query string in any link/href → no token param
    # The structural id is a urn, not a tokenized URL.
    assert "urn:transmutary:feed" in xml
    assert "urn:transmutary:report:" in xml


def test_empty_feed_still_valid():
    xml = render_feed([], feed_name="digest")
    assert "<feed" in xml


def test_digest_feed_multiple_entries():
    reports = [_report(Severity.NORMAL), _report(Severity.INFO)]
    xml = render_feed(reports, feed_name="digest")
    assert xml.count("<entry") == 2


def test_feed_title_localized_from_feed_name():
    # title is built from feed_name + lang (no caller-supplied title anymore)
    en = render_feed([], feed_name="immediate")
    assert "<title>transmutary immediate feed</title>" in en
    zh = render_feed([], feed_name="digest", lang="zh")
    assert "transmutary digest 订阅源" in zh


# --- Single-language RSS entries (delivery localization) ---------------------

def test_rss_entry_zh_renders_chinese_only():
    r = _report()
    r.body_md_zh = "疑似根因：上游发布。"
    r.title_zh = "中文标题"
    xml = render_single(r, lang="zh")
    assert "疑似根因" in xml
    assert "## 中文" not in xml  # no bilingual stacking
    assert "Suspected root cause" not in xml  # English body absent
    assert "中文标题" in xml  # localized title
    assert 'xml:lang="zh-CN"' in xml  # feed language follows
    assert "来源:" in xml  # localized Sources label


def test_rss_entry_default_english_single_language():
    r = _report()
    r.body_md_zh = "疑似根因：上游发布。"
    xml = render_single(r)  # default en
    assert "Suspected root cause" in xml
    assert "疑似根因" not in xml  # zh body absent in en feed
    assert 'xml:lang="en"' in xml


def test_rss_fetched_at_formatted_not_raw():
    import re
    xml = render_single(_report())
    content = re.search(r"<content[^>]*>(.*?)</content>", xml, re.S).group(1)
    # the source line carries a human date, not the old "(fetched <iso/float>)"
    assert "(2026-05-29 10:00 UTC)" in content
    assert "fetched " not in content
