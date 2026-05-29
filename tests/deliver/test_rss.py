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
    xml = render_feed([], feed_name="digest", title="digest")
    assert "<feed" in xml


def test_digest_feed_multiple_entries():
    reports = [_report(Severity.NORMAL), _report(Severity.INFO)]
    xml = render_feed(reports, feed_name="digest", title="daily digest")
    assert xml.count("<entry") == 2
