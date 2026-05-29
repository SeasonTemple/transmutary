"""U10 clean.py tests — structural checks before LLM + paragraph trimming (R17)."""

from __future__ import annotations

from transmutary.clean import (
    CleanInput,
    clean_batch,
    clean_item,
    is_reachable,
    is_stale,
    trim_relevant_paragraphs,
)


def test_unreachable_empty_content_dropped():
    res = clean_item(CleanInput(repo="acme/cli", text="   ", url="https://x/y"))
    assert res.kept is False
    assert "unreachable" in res.reason


def test_stale_content_dropped_before_llm():
    item = CleanInput(
        repo="acme/cli",
        text="500 errors observed",
        url="https://github.com/acme/cli/issues/1",
        ts="2020-01-01T00:00:00Z",
    )
    res = clean_item(item, anchor_ts="2026-05-20T00:00:00Z")
    assert res.kept is False
    assert "stale" in res.reason


def test_fresh_relevant_content_kept():
    item = CleanInput(
        repo="acme/cli",
        text="The gateway is returning 504 timeout errors for all requests.",
        ts="2026-05-19T00:00:00Z",
    )
    res = clean_item(item, anchor_ts="2026-05-20T00:00:00Z")
    assert res.kept is True
    assert "504" in res.text


def test_is_stale_unparseable_ts_not_stale():
    # Malformed timestamp must not silently drop content.
    assert is_stale("not-a-date", "2026-05-20T00:00:00Z", window_secs=10) is False


def test_is_reachable_requires_body():
    assert is_reachable(CleanInput(repo="r", text="", url="https://x")) is False
    assert is_reachable(CleanInput(repo="r", text="hi", url="")) is True


def test_paragraph_trimming_keeps_relevant_drops_noise():
    text = (
        "Welcome to my personal blog about cooking.\n\n"
        "Today the acme/cli gateway returned 503 errors and timeouts.\n\n"
        "Here is a recipe for soup."
    )
    out = trim_relevant_paragraphs(text, ["acme/cli"])
    assert "503" in out
    assert "recipe for soup" not in out
    assert "cooking" not in out


def test_paragraph_trimming_falls_back_when_nothing_matches():
    # Coarse rule matched nothing relevant → keep all paragraphs (no data loss).
    text = "alpha paragraph here.\n\nbeta paragraph here."
    out = trim_relevant_paragraphs(text, ["zzz-no-match"])
    assert "alpha" in out and "beta" in out


def test_clean_batch_drops_failures_keeps_survivors():
    items = [
        CleanInput(repo="r", text="", url="u1"),  # unreachable
        CleanInput(repo="r", text="504 outage", ts="2026-05-19T00:00:00Z"),  # ok
        CleanInput(repo="r", text="old 500", ts="2000-01-01T00:00:00Z"),  # stale
    ]
    kept = clean_batch(items, anchor_ts="2026-05-20T00:00:00Z")
    assert len(kept) == 1
    assert "504" in kept[0].text
