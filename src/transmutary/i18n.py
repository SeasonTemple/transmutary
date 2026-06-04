"""Core i18n primitives — language constants + outbound-delivery chrome strings.

Lowest layer (no internal imports) so every layer can depend on it without the
backwards coupling the delivery renderers previously avoided: ``config``,
``report``, ``deliver``, and ``dashboard`` all import from here. The dashboard's
large UI ``MESSAGES`` table stays in ``dashboard/i18n.py`` (presentation-only);
this module owns only what the delivery layer also needs.

Delivery email/RSS is single-language (a push carries ONE language; the
dashboard report view and RSS are configured per deployment). ``DELIVERY_STRINGS``
holds the human chrome rendered around report bodies. Severity labels are
intentionally NOT here — ``HIGH``/``CRITICAL`` stay as universal English labels,
and the email subject keeps a machine-readable ``[transmutary/<severity>]`` tag
so operators can filter on it.
"""

from __future__ import annotations

# Supported languages. A value outside this set falls back to DEFAULT_LANG.
SUPPORTED_LANGS = ("en", "zh")
DEFAULT_LANG = "en"

# Maps a supported lang to the BCP-47 code used in ``lang=`` / feed ``xml:lang``.
HTML_LANG = {"en": "en", "zh": "zh-CN"}

# Outbound-delivery chrome (email + RSS). Templates use ``str.format`` named
# fields. Keep keys identical across langs so ``delivery_strings`` is total.
DELIVERY_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "sources": "Sources",
        "daily_digest": "Daily Digest",
        "digest_overview": "{n} report(s) in the last 24h",
        "digest_high_risk": " — {n} high-risk",
        "no_reports": "No reports in the last 24h.",
        "unverified_prefix": "[Unverified] ",
        "trend_title": "Trend: {repo}",
        "feed_title": "transmutary {name} feed",
    },
    "zh": {
        "sources": "来源",
        "daily_digest": "每日摘要",
        "digest_overview": "过去 24 小时 {n} 份报告",
        "digest_high_risk": " — {n} 高危",
        "no_reports": "过去 24 小时无报告。",
        "unverified_prefix": "[待核实信号] ",
        "trend_title": "趋势：{repo}",
        "feed_title": "transmutary {name} 订阅源",
    },
}


def delivery_strings(lang: str) -> dict[str, str]:
    """Return the delivery chrome string table for ``lang`` (fallback to default)."""
    return DELIVERY_STRINGS.get(lang, DELIVERY_STRINGS[DEFAULT_LANG])


__all__ = (
    "DEFAULT_LANG",
    "DELIVERY_STRINGS",
    "HTML_LANG",
    "SUPPORTED_LANGS",
    "delivery_strings",
)
