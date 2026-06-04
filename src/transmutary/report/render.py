"""Markdown → HTML rendering for report bodies (R2/R3/R4).

Shared by the dashboard (WebUI report view) and the email/digest delivery layer.
Lives under ``report/`` (common upstream of ``dashboard/`` and ``deliver/``) to
avoid a cross-layer import. LLM report bodies are UNTRUSTED markdown; this renders
them to HTML safely without a heavyweight sanitizer:

  * ``MarkdownIt("commonmark", {"html": False})`` escapes ALL raw HTML — a
    ``<script>``/``<img onerror>``/``<div onclick>`` in the source becomes inert
    text, never a live tag. So no untrusted tag can reach the DOM.
  * markdown-it-py's default ``validateLink`` rejects ``javascript:`` /
    ``vbscript:`` / ``data:text/html`` (and case / tab / leading-space variants),
    so a crafted link cannot smuggle script. Verified against markdown-it-py 3.0.

The rendered output therefore contains only markdown-it's own generated tags
(``<h1>``/``<ul>``/``<a href="http…">``/``<code>`` …) and is safe to emit through
Jinja ``| safe``. This holds for the dashboard (CSP ``style-src 'self'`` forbids
inline style/script anyway) and for email HTML (which renders the same output).
"""

from __future__ import annotations

import datetime

from markdown_it import MarkdownIt

from .schema import Report

# A single shared parser instance: stateless across renders, cheap to reuse.
# html=False is the load-bearing security switch (raw HTML → escaped text).
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})


def render_markdown(text: str | None) -> str:
    """Render untrusted markdown to safe HTML. Empty/None → empty string."""
    if not text:
        return ""
    return _MD.render(text)


def localized_body(report: Report, lang: str) -> tuple[str, str]:
    """Pick one language's body for single-language delivery (email).

    Returns ``(markdown_body, html_lang_attr)``. ``lang == "zh"`` uses the Chinese
    body, falling back to English when no translation exists; any other value uses
    English. Email is a single-language push (the dashboard/RSS keep both).
    """
    if lang == "zh" and report.body_md_zh:
        return report.body_md_zh, "zh-CN"
    return report.body_md, "en"


def localized_title(report: Report, lang: str) -> str:
    """Pick one language's title for single-language delivery (email/RSS).

    ``lang == "zh"`` uses ``title_zh`` when present, else falls back to ``title``
    (external/untranslatable titles leave ``title_zh`` None). Any other lang uses
    ``title``. Mirrors :func:`localized_body`.
    """
    if lang == "zh" and report.title_zh:
        return report.title_zh
    return report.title


def fmt_timestamp(value: str | float | int) -> str:
    """Human-readable ``YYYY-MM-DD HH:MM UTC`` from an ISO-8601 string or epoch.

    Unparseable input is returned as ``str(value)`` (never raises) so a delivery
    render can't crash on a malformed timestamp.
    """
    try:
        if isinstance(value, (int, float)):
            dt = datetime.datetime.utcfromtimestamp(value)
        else:
            dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OSError, OverflowError):
        return str(value)
