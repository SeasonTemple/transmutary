"""Bilingual body parser tests (KTD2)."""

from __future__ import annotations

from transmutary.report.body_parse import parse_bilingual


def test_split_en_zh():
    en, zh = parse_bilingual("English text<!-- BILINGUAL:SPLIT -->Chinese text")
    assert en == "English text"
    assert zh == "Chinese text"


def test_no_marker_returns_none():
    en, zh = parse_bilingual("English only")
    assert en == "English only"
    assert zh is None


def test_empty_input():
    en, zh = parse_bilingual("")
    assert en == ""
    assert zh is None


def test_marker_only():
    en, zh = parse_bilingual("<!-- BILINGUAL:SPLIT -->")
    assert en == ""
    assert zh == ""


def test_marker_with_empty_en():
    en, zh = parse_bilingual("<!-- BILINGUAL:SPLIT -->ZH only")
    assert en == ""
    assert zh == "ZH only"


def test_marker_with_empty_zh():
    en, zh = parse_bilingual("EN only<!-- BILINGUAL:SPLIT -->")
    assert en == "EN only"
    assert zh == ""
