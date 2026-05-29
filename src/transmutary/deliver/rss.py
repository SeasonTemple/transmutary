"""Private RSS/Atom feed generation (U15, R14/R15/R16/R20).

``feedgen`` renders a ``Report`` (and report batches) into an Atom feed. There are
two feeds in MVP, matching the inline two-branch route (KTD1 — NO channel
abstraction):

  * **immediate** — one entry per high-risk report (mode A diagnosis / supply-
    chain alert), pushed as soon as it is produced.
  * **digest** — accumulated low-priority / mode-B explanations served as a daily
    digest feed.

R20: the token NEVER appears in the feed URL or in any entry — authentication is
an HTTP ``Authorization`` header handled by :mod:`transmutary.deliver.server`. This
module only renders content; it holds no credentials.
"""

from __future__ import annotations

from feedgen.feed import FeedGenerator

from ..report.schema import Report

# Feed identity. The feed id is a stable urn (NOT a tokenized URL) — R20: no token
# is ever embedded in feed metadata or entry links.
_FEED_ID_BASE = "urn:transmutary:feed"


def _entry_id(report: Report) -> str:
    safe_repo = report.repo.replace("/", "_")
    return f"urn:transmutary:report:{safe_repo}:{report.created_at}:{report.kind.value}"


def _render_entry(fg: FeedGenerator, report: Report) -> None:
    fe = fg.add_entry()
    fe.id(_entry_id(report))
    fe.title(report.title)
    fe.updated(report.created_at)
    # Body as content; source URLs (non-secret) listed in the content, not as
    # tokenized links (R20).
    body = report.body_md
    if report.sources:
        lines = [body, "", "Sources:"]
        for s in report.sources:
            lines.append(f"- {s.source_id}: {s.url} (fetched {s.fetched_at})")
        body = "\n".join(lines)
    fe.content(content=body, type="text")
    # categories carry severity/kind so a reader can triage without a token-bearing link.
    fe.category(term=report.severity.value, label="severity")
    fe.category(term=report.kind.value, label="kind")


def render_feed(reports: list[Report], *, feed_name: str, title: str) -> str:
    """Render a list of reports into an Atom feed XML string (R20-safe).

    ``feed_name`` selects the logical feed (``immediate`` / ``digest``); it becomes
    part of the feed's urn id but is never a token.
    """
    fg = FeedGenerator()
    fg.id(f"{_FEED_ID_BASE}:{feed_name}")
    fg.title(title)
    fg.link(href=f"{_FEED_ID_BASE}:{feed_name}", rel="self")
    fg.language("en")
    # feedgen requires at least one author at the feed level.
    fg.author({"name": "transmutary"})
    # Most-recent first.
    for report in reports:
        _render_entry(fg, report)
    if not reports:
        # feedgen needs an updated timestamp; an empty feed still validates.
        fg.updated("1970-01-01T00:00:00+00:00")
    return fg.atom_str(pretty=True).decode("utf-8")


def render_single(report: Report, *, feed_name: str = "immediate") -> str:
    """Render one report as a single-entry feed (the immediate-push case)."""
    return render_feed(
        [report], feed_name=feed_name, title=f"transmutary {feed_name} feed"
    )
