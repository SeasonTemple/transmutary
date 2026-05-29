"""U8 dedup.py tests — fingerprints, clustering+escalation (AE4), R18 merge.

Uses an in-memory StateStore. No real network.
"""

from __future__ import annotations

import pytest

from transmutary.dedup import (
    DEFAULT_WINDOW_SECONDS,
    SourceItem,
    canonicalize_url,
    dedup_issue,
    dedup_release,
    extract_upstream_refs,
    issue_cluster_fingerprint,
    keyword_bucket,
    merge_references,
)
from transmutary.store.state import StateStore


@pytest.fixture
def store():
    s = StateStore(":memory:")
    try:
        yield s
    finally:
        s.close()


# --- AE4: release dedup across cycles ---------------------------------------
def test_same_release_across_cycles_emits_once(store):
    d1 = dedup_release(store, "acme/cli", "v1.0.0", url="https://github.com/acme/cli/r/v1.0.0")
    d2 = dedup_release(store, "acme/cli", "v1.0.0", url="https://github.com/acme/cli/r/v1.0.0")
    assert d1.is_new is True
    assert d2.is_new is False  # second cycle suppressed
    assert d1.fingerprint == d2.fingerprint


# --- AE4: issue clustering + escalation -------------------------------------
def test_issue_cluster_accumulates_evidence(store):
    ts = 1_000_000.0
    decisions = []
    for i in range(3):
        decisions.append(
            dedup_issue(store, "acme/cli", f"service is down #{i}", ts, url=f"u{i}")
        )
    assert decisions[0].is_new is True
    assert decisions[1].is_new is False  # same cluster
    assert [d.evidence_count for d in decisions] == [1, 2, 3]
    assert all(not d.escalated for d in decisions)  # below threshold


def test_issue_cluster_escalates_once_at_threshold(store):
    ts = 2_000_000.0
    escalations = []
    for i in range(7):
        d = dedup_issue(
            store, "acme/cli", f"outage timeout #{i}", ts, url=f"u{i}", escalation_threshold=5
        )
        escalations.append(d.escalated)
    # Escalation fires exactly once, on the pass that crosses the threshold.
    assert escalations.count(True) == 1
    assert escalations[4] is True  # 5th issue crosses count>=5


def test_duplicate_issue_no_double_count(store):
    ts = 3_000_000.0
    d1 = dedup_issue(store, "acme/cli", "down", ts, url="same-url")
    d2 = dedup_issue(store, "acme/cli", "down", ts, url="same-url")  # exact dup
    assert d1.evidence_count == 1
    assert d2.evidence_count == 1  # not bumped
    assert d2.is_new is False


def test_rolling_window_rollover_new_event(store):
    text = "service down"
    win0 = 1_000_000.0
    win_next = win0 + DEFAULT_WINDOW_SECONDS  # next window bucket
    fp0 = issue_cluster_fingerprint("acme/cli", text, win0)
    fp1 = issue_cluster_fingerprint("acme/cli", text, win_next)
    assert fp0 != fp1  # different window → different event


# --- URL canonicalization ---------------------------------------------------
def test_url_canonicalization_merges():
    a = canonicalize_url("https://github.com/acme/cli/issues/9/")
    b = canonicalize_url("https://github.com/acme/cli/issues/9?utm=x")
    c = canonicalize_url("https://www.github.com/acme/cli/issues/9")
    assert a == b == c


# --- R18: reference-URL merge / source independence -------------------------
def test_three_blogs_one_upstream_count_as_one(store):
    upstream = "https://github.com/acme/cli/issues/42"
    items = [
        SourceItem(url="https://blog-a.com/post", text=f"see {upstream}"),
        SourceItem(url="https://blog-b.net/x", text=f"reported in {upstream}"),
        SourceItem(url="https://blog-c.org/y", text=f"per {upstream} this is bad"),
    ]
    result = merge_references(items)
    # All three blogs merge onto the single upstream → 1 independent source.
    assert result.independent_source_count() == 1
    # They all resolve to the same canonical cluster.
    cid = result.canonical_id_for("https://blog-a.com/post")
    assert cid == result.canonical_id_for("https://blog-c.org/y")
    assert result.independent_source_count() < 2  # cannot pass >=2 gate (R18)


def test_multi_cite_decoy_upstream_does_not_inflate_count():
    # R18 multi-cite spoof: three blogs each name a DISTINCT decoy upstream first
    # but all also cite ONE shared real upstream. Keying on only the first cited
    # ref would split these into 3 sources; sharing the real upstream must
    # collapse them into 1.
    real = "https://github.com/acme/cli/issues/42"
    items = [
        SourceItem(
            url="https://blog-a.com/post",
            text=f"ref https://github.com/x/y/issues/1 ... real {real}",
        ),
        SourceItem(
            url="https://blog-b.net/x",
            text=f"ref https://github.com/x/y/issues/2 ... real {real}",
        ),
        SourceItem(
            url="https://blog-c.org/y",
            text=f"ref https://github.com/x/y/issues/3 ... real {real}",
        ),
    ]
    result = merge_references(items)
    # All three share the real upstream → ONE independent source, cannot fake >=2.
    assert result.independent_source_count() == 1


def test_two_genuinely_independent_sources_count_two():
    items = [
        SourceItem(url="https://github.com/acme/cli/issues/42"),  # upstream itself
        SourceItem(url="https://unrelated.com/standalone-report"),  # standalone, no cite
    ]
    result = merge_references(items)
    assert result.independent_source_count() == 2


def test_extract_upstream_refs():
    text = (
        "Discussed in https://github.com/acme/cli/pull/7 and "
        "advisory https://github.com/advisories/GHSA-aaaa-bbbb-cccc ."
    )
    refs = extract_upstream_refs(text)
    assert any("/pull/7" in r for r in refs)
    assert any("GHSA-aaaa-bbbb-cccc" in r for r in refs)


def test_keyword_bucket_multilingual():
    assert keyword_bucket("the service is down") == "outage"
    assert keyword_bucket("接口挂了") == "outage"
    assert keyword_bucket("CVE-2026-1 vulnerability") == "security"
    assert keyword_bucket("just a question") == "other"
