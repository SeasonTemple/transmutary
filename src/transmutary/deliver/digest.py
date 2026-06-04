"""Daily aggregate digest (R10) — summarise the last 24h of reports.

A once-daily job (scheduled at ``delivery.digest_hour``) that gathers every report
written across all repos within a fixed look-back window, renders ONE HTML summary
(designed layout, CJK + a11y), writes it as an artifact, and ships it over RSS
(batch feed, reusing :func:`rss.render_feed`) and email (multipart, reusing the
email HTML path).

Windowing is by timestamp (``ReportRef.ts >= now - WINDOW``), not a delivered-
marker: the job runs at a fixed hour over a fixed window, so it is idempotent
without persistent per-report state.
"""

from __future__ import annotations

from html import escape

from ..report.render import render_markdown
from ..report.schema import Report, Severity
from ..store.artifacts import ArtifactStore

WINDOW_SECONDS = 24 * 60 * 60

# Severity display order (most urgent first) for digest grouping.
_SEV_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.NORMAL: 2, Severity.INFO: 3}
_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
    "'Microsoft YaHei','Noto Sans CJK SC','Hiragino Sans GB',sans-serif"
)
_SEV_STYLE = {
    "critical": "background:#fde8e8;color:#9b1c1c;",
    "high": "background:#fef3e2;color:#9a3412;",
    "info": "background:#e0f2fe;color:#075985;",
    "normal": "background:#eef1f4;color:#374151;",
}


def collect_digest_reports(
    artifacts: ArtifactStore, now_ts: float, *, window: int = WINDOW_SECONDS
) -> list[Report]:
    """Gather reports across all repos with ``ts >= now_ts - window`` (R10).

    Sorted most-urgent-first, then newest-first. Malformed sidecars are skipped.
    """
    cutoff = now_ts - window
    collected: list[tuple[int, int, Report]] = []  # (sev_order, -ts, report)
    for repo in artifacts.list_repos():
        for ref in artifacts.list_reports(repo):
            if ref.ts < cutoff:
                continue
            meta = artifacts.read_meta(repo, ref.filename)
            if meta is None:
                continue
            try:
                report = Report.from_dict(meta)
            except (KeyError, ValueError):
                continue  # skip malformed/partial sidecar, don't crash the digest
            collected.append((_SEV_ORDER.get(report.severity, 9), -ref.ts, report))
    collected.sort(key=lambda t: (t[0], t[1]))
    return [r for _, _, r in collected]


def _report_block(report: Report) -> str:
    sev = report.severity.value
    sev_style = _SEV_STYLE.get(sev, _SEV_STYLE["normal"])
    badge = (
        f'<span style="display:inline-block;padding:.1rem .5rem;border-radius:5px;'
        f'font-size:.72rem;font-weight:600;{sev_style}">{escape(sev.upper())}</span>'
    )
    en = render_markdown(report.body_md)
    zh = (
        f'<article lang="zh-CN" style="margin:.5rem 0 0;">'
        f"{render_markdown(report.body_md_zh)}</article>"
        if report.body_md_zh
        else ""
    )
    return (
        f'<article style="border:1px solid #d0d7de;border-radius:8px;'
        f'padding:1rem 1.25rem;margin:0 0 1rem;">'
        f'<div style="margin:0 0 .5rem;">{badge} '
        f'<code style="color:#636c76;font-size:.82rem;">{escape(report.repo)}</code></div>'
        f'<h2 style="font-size:1.15rem;line-height:1.3;margin:0 0 .5rem;">'
        f"{escape(report.title)}</h2>"
        f'<article lang="en" style="line-height:1.7;word-break:break-word;">{en}</article>'
        f"{zh}</article>"
    )


def render_digest_html(reports: list[Report], *, date_label: str) -> str:
    """Render the aggregate digest as standalone HTML (R8 CJK, R9 a11y)."""
    urgent = sum(1 for r in reports if r.severity.is_urgent)
    overview = (
        f"{len(reports)} report(s) in the last 24h"
        f"{f' — {urgent} high-risk' if urgent else ''}"
    )
    blocks = "".join(_report_block(r) for r in reports) or (
        '<p style="color:#636c76;">No reports in the last 24h.</p>'
    )
    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Daily Digest {escape(date_label)}</title></head>"
        f'<body style="margin:0;padding:0;background:#f6f8fa;font-family:{_FONT};color:#1f2328;">'
        f'<main style="max-width:720px;margin:0 auto;padding:1.5rem;">'
        f'<h1 style="font-size:1.5rem;margin:0 0 .25rem;">Daily Digest {escape(date_label)}</h1>'
        f'<p style="color:#636c76;margin:0 0 1.5rem;font-size:.9rem;">{escape(overview)}</p>'
        f"{blocks}</main></body></html>"
    )


def render_digest_text(reports: list[Report], *, date_label: str) -> str:
    """Plain-text digest fallback."""
    lines = [f"Daily Digest {date_label}", f"{len(reports)} report(s) in the last 24h", ""]
    for r in reports:
        lines.append(f"[{r.severity.value.upper()}] {r.repo} — {r.title}")
        lines.append(r.body_md)
        if r.body_md_zh:
            lines.append("\n## 中文\n" + r.body_md_zh)
        lines.append("\n---\n")
    return "\n".join(lines)
