"""Dashboard UI i18n (U2, R-I1/R-I3/R-I4).

Single source of truth for UI *chrome* strings (nav, section titles, column
headers, buttons, empty states). Data content — repo names, severity, kind,
report titles/bodies — is NEVER translated (R-I2); it stays original.

Two consumers share this dictionary: server-side Jinja (first paint, no FOUC) and
client-side JS (in-page switching). The JS dictionary mirrors these keys; a test
asserts both sides carry the same key set so they cannot drift.
"""

from __future__ import annotations

# Supported languages. A cookie value outside this set falls back to DEFAULT_LANG
# (R-I4 — no arbitrary cookie value reaches the template / <html lang>).
SUPPORTED_LANGS = ("en", "zh")
DEFAULT_LANG = "en"

# UI chrome only. Keys are dotted namespaces. en and zh MUST have identical keys.
MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "nav.overview": "Overview",
        "nav.alerts": "Alerts",
        "nav.trends": "Trends",
        "nav.reports": "Reports",
        "page.overview": "Overview",
        "ro": "read-only",
        "rw": "writes enabled",
        "stat.repos": "Watched repos",
        "stat.alerts": "Supply-chain alerts",
        "stat.trends": "Trend candidates",
        "stat.reports": "Recent reports",
        "stat.attn": "needs attention",
        "sec.alerts": "Supply-chain alerts",
        "sec.watchlist": "Watchlist",
        "sec.trends": "Trend candidates",
        "sec.reports": "Recent reports",
        "sec.feeds": "Feeds",
        "sec.runtime": "Runtime state",
        "sec.sources": "Sources",
        "sec.report": "Report",
        "note.watchlist": "config ∪ promoted",
        "note.feeds": "token via Authorization header, not shown",
        "th.repo": "Repo",
        "th.source": "Source",
        "th.stars": "Stars",
        "th.growth": "Growth",
        "th.kind": "Kind",
        "th.severity": "Severity",
        "th.title": "Title",
        "empty.alerts": "No supply-chain alerts",
        "empty.trends": "No trend candidates",
        "empty.reports": "No reports yet",
        "empty.watchlist": "No repos observed",
        "empty.repo_reports": "No reports for this repo",
        "field.baseline": "Issue baseline (rate)",
        "field.stars": "Latest stars",
        "field.growth": "Star growth",
        "field.cursor": "Collect cursor",
        "tag.observed": "observed",
        "tag.archived": "archived only",
        "theme": "Theme",
        "theme.light": "Light",
        "theme.dark": "Dark",
        "theme.system": "System",
        "lang.toggle": "中文",
        "back.overview": "Overview",
        "promote": "Promote",
        "demote": "Demote",
        "confirm": "Confirm",
        "cancel": "Cancel",
        "confirm_promote_q": "Promote this repo into the watchlist?",
        "confirm_demote_q": "Remove this promoted repo from the watchlist?",
        "write_busy": "The state database is busy. Try again in a moment.",
        "error_csrf": "Request rejected. Refresh the page and try again.",
        "error_invalid_repo": "Invalid repository name. Expected owner/repo.",
        "error_not_promoted": "Only promoted repos can be removed from the dashboard.",
    },
    "zh": {
        "nav.overview": "总览",
        "nav.alerts": "告警",
        "nav.trends": "趋势",
        "nav.reports": "报告",
        "page.overview": "总览",
        "ro": "只读",
        "rw": "写入",
        "stat.repos": "关注仓库",
        "stat.alerts": "供应链告警",
        "stat.trends": "趋势候选",
        "stat.reports": "最近报告",
        "stat.attn": "需关注",
        "sec.alerts": "供应链告警",
        "sec.watchlist": "关注清单",
        "sec.trends": "趋势候选",
        "sec.reports": "最近报告",
        "sec.feeds": "订阅源",
        "sec.runtime": "运行态",
        "sec.sources": "来源",
        "sec.report": "报告",
        "note.watchlist": "config ∪ 晋升",
        "note.feeds": "token 经 Authorization 头传递，不展示",
        "th.repo": "仓库",
        "th.source": "来源",
        "th.stars": "星标",
        "th.growth": "增速",
        "th.kind": "类型",
        "th.severity": "严重度",
        "th.title": "标题",
        "empty.alerts": "无供应链告警",
        "empty.trends": "无趋势候选",
        "empty.reports": "暂无报告",
        "empty.watchlist": "无观测仓库",
        "empty.repo_reports": "该仓库暂无报告",
        "field.baseline": "Issue 基线（速率）",
        "field.stars": "最新星标",
        "field.growth": "星标增长",
        "field.cursor": "采集游标",
        "tag.observed": "观测中",
        "tag.archived": "仅归档",
        "theme": "主题",
        "theme.light": "亮色",
        "theme.dark": "暗色",
        "theme.system": "跟随系统",
        "lang.toggle": "EN",
        "back.overview": "总览",
        "promote": "晋升",
        "demote": "取消晋升",
        "confirm": "确认",
        "cancel": "取消",
        "confirm_promote_q": "将该仓库晋升进关注清单？",
        "confirm_demote_q": "从关注清单移除该晋升仓库？",
        "write_busy": "状态数据库正忙，请稍后重试。",
        "error_csrf": "请求已拒绝。请刷新页面后重试。",
        "error_invalid_repo": "仓库名无效，应为 owner/repo。",
        "error_not_promoted": "只有已晋升仓库可从看板取消。",
    },
}

LANG_COOKIE = "tmtry-lang"
# Map internal lang code to the <html lang> attribute value.
HTML_LANG = {"en": "en", "zh": "zh-CN"}


def resolve_lang(cookie_value: str | None) -> str:
    """Resolve a cookie value to a supported lang, else DEFAULT_LANG (R-I4).

    Strict whitelist — any value outside SUPPORTED_LANGS (injection attempts,
    unknown locales, oversized junk) returns the default. No arbitrary cookie
    value ever flows into the rendered template or the <html lang> attribute.
    """
    if cookie_value in SUPPORTED_LANGS:
        return cookie_value
    return DEFAULT_LANG


def messages_for(lang: str) -> dict[str, str]:
    """Return the message dict for ``lang`` (assumes already resolved)."""
    return MESSAGES.get(lang, MESSAGES[DEFAULT_LANG])
