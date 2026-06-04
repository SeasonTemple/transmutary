"""Core i18n module — delivery chrome strings + shared language constants."""

from __future__ import annotations

import pytest

from transmutary import i18n


def test_delivery_strings_en_and_zh():
    assert i18n.delivery_strings("en")["sources"] == "Sources"
    assert i18n.delivery_strings("zh")["sources"] == "来源"


def test_delivery_strings_unknown_lang_falls_back_to_default():
    assert i18n.delivery_strings("fr") == i18n.delivery_strings(i18n.DEFAULT_LANG)


def test_delivery_strings_keys_identical_across_langs():
    assert set(i18n.DELIVERY_STRINGS["en"]) == set(i18n.DELIVERY_STRINGS["zh"])


def test_delivery_strings_omit_severity_labels():
    # Severity stays English/universal — never localized (Finding A). No table
    # entry maps a severity LEVEL to a translated word.
    forbidden = {"critical", "high", "normal", "info", "severity"}
    for lang in i18n.SUPPORTED_LANGS:
        assert forbidden.isdisjoint(i18n.delivery_strings(lang).keys())


@pytest.mark.parametrize("template_key,field", [
    ("digest_overview", "n"),
    ("digest_high_risk", "n"),
    ("trend_title", "repo"),
    ("feed_title", "name"),
])
def test_delivery_string_templates_format(template_key, field):
    # Templates carry their named field and interpolate without KeyError.
    for lang in i18n.SUPPORTED_LANGS:
        i18n.delivery_strings(lang)[template_key].format(**{field: "x"})


def test_constants_single_source_dashboard():
    from transmutary.dashboard import i18n as dash
    assert dash.SUPPORTED_LANGS is i18n.SUPPORTED_LANGS
    assert dash.DEFAULT_LANG == i18n.DEFAULT_LANG
    assert dash.HTML_LANG is i18n.HTML_LANG


def test_constants_single_source_config():
    from transmutary import config
    assert config.SUPPORTED_EMAIL_LANGS is i18n.SUPPORTED_LANGS
    assert config.DEFAULT_EMAIL_LANG == i18n.DEFAULT_LANG
