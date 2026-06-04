"""Report → standalone HTML email body (R5/R8/R9).

Renders a ``Report`` into a self-contained HTML document for the email
``text/html`` alternative. Email clients strip ``<style>``/external CSS
unreliably, so all styling is INLINE (this is an email-client requirement, not a
dashboard-CSP concern — email HTML never passes through the dashboard).

Design constraints:
  * R8 CJK — CJK-capable font stack; ``lang`` on the English/Chinese sections;
    line-height + word-break tuned for mixed CJK/Latin text.
  * R9 a11y — semantic ``<main>``/``<article>``/headings; severity conveyed by
    TEXT (not colour alone); AA-contrast colours; underlined links.

Body markdown is UNTRUSTED and rendered via :func:`report.render.render_markdown`
(html=False → raw HTML escaped, dangerous link schemes rejected).
"""

from __future__ import annotations

from html import escape

from ..report.render import render_markdown
from ..report.schema import Report

_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
    "'Microsoft YaHei','Noto Sans CJK SC','Hiragino Sans GB',sans-serif"
)
# AA-contrast severity colours (text + background), severity also stated in words.
_SEV_STYLE = {
    "critical": "background:#fde8e8;color:#9b1c1c;",
    "high": "background:#fef3e2;color:#9a3412;",
    "info": "background:#e0f2fe;color:#075985;",
    "normal": "background:#eef1f4;color:#374151;",
}


def _section(title: str, body_md: str | None, lang: str) -> str:
    if not body_md:
        return ""
    inner = render_markdown(body_md)
    return (
        f'<article lang="{lang}" style="margin:0 0 1.5rem;line-height:1.75;'
        f'word-break:break-word;overflow-wrap:anywhere;">'
        f'<div>{inner}</div></article>'
    )


def render_email_html(report: Report) -> str:
    """Render a Report into a standalone HTML email body (R5/R8/R9)."""
    sev = report.severity.value
    sev_style = _SEV_STYLE.get(sev, _SEV_STYLE["normal"])
    title = escape(report.title)
    repo = escape(report.repo)
    # Severity badge: word + colour (R9 — never colour alone).
    badge = (
        f'<span style="display:inline-block;padding:.15rem .6rem;border-radius:6px;'
        f'font-size:.78rem;font-weight:600;{sev_style}">'
        f'{escape(sev.upper())}</span>'
    )
    en = _section("English", report.body_md, "en")
    zh = _section("中文", report.body_md_zh, "zh-CN")
    sources_html = ""
    if report.sources:
        items = "".join(
            f'<li style="margin:.25rem 0;"><code>{escape(s.source_id)}</code> '
            f'<a href="{escape(s.url)}" style="color:#0969da;">{escape(s.url)}</a></li>'
            for s in report.sources
        )
        sources_html = (
            '<h2 style="font-size:1.1rem;margin:1.5rem 0 .5rem;">Sources</h2>'
            f'<ul style="padding-left:1.3rem;margin:0;">{items}</ul>'
        )
    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title></head>"
        f'<body style="margin:0;padding:0;background:#f6f8fa;font-family:{_FONT};color:#1f2328;">'
        f'<main style="max-width:680px;margin:0 auto;padding:1.5rem;">'
        f'<div style="background:#fff;border:1px solid #d0d7de;border-radius:8px;'
        f'padding:1.5rem 1.75rem;">'
        f'<div style="margin:0 0 .75rem;">{badge}</div>'
        f'<h1 style="font-size:1.4rem;line-height:1.3;margin:0 0 .35rem;">{title}</h1>'
        f'<p style="margin:0 0 1.25rem;color:#636c76;font-size:.9rem;">'
        f'<code>{repo}</code> &middot; {escape(report.created_at)}</p>'
        f"{en}{zh}{sources_html}"
        f"</div></main></body></html>"
    )


def render_email_text(report: Report) -> str:
    """Plain-text email fallback: English MD + Chinese MD + sources."""
    parts = [report.body_md or ""]
    if report.body_md_zh:
        parts.append("\n\n## 中文\n\n" + report.body_md_zh)
    if report.sources:
        parts.append(
            "\n\nSources:\n"
            + "\n".join(
                f"- {s.source_id}: {s.url} (fetched {s.fetched_at})" for s in report.sources
            )
        )
    return "".join(parts)
