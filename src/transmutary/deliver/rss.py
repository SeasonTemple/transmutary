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

from ..i18n import DEFAULT_LANG, HTML_LANG, delivery_strings
from ..report.render import fmt_timestamp, localized_body, localized_title
from ..report.schema import Report
from .routes import DeliveryRoute

# Feed identity. The feed id is a stable urn (NOT a tokenized URL) — R20: no token
# is ever embedded in feed metadata or entry links.
_FEED_ID_BASE = "urn:transmutary:feed"


def _entry_id(report: Report) -> str:
    safe_repo = report.repo.replace("/", "_")
    return f"urn:transmutary:report:{safe_repo}:{report.created_at}:{report.kind.value}"


def _render_entry(fg: FeedGenerator, report: Report, lang: str) -> None:
    fe = fg.add_entry()
    fe.id(_entry_id(report))
    fe.title(localized_title(report, lang))
    fe.updated(report.created_at)
    # Single-language content (a feed is configured per deployment; matches the
    # email push). Source URLs (non-secret) listed in the content, not as
    # tokenized links (R20).
    body, _ = localized_body(report, lang)
    if report.sources:
        lines = [body, "", f"{delivery_strings(lang)['sources']}:"]
        for s in report.sources:
            lines.append(f"- {s.source_id}: {s.url} ({fmt_timestamp(s.fetched_at)})")
        body = "\n".join(lines)
    fe.content(content=body, type="text")
    # categories carry severity/kind so a reader can triage without a token-bearing link.
    fe.category(term=report.severity.value, label="severity")
    fe.category(term=report.kind.value, label="kind")


def render_feed(
    reports: list[Report],
    *,
    feed_name: str,
    title: str | None = None,
    lang: str = DEFAULT_LANG,
) -> str:
    """Render a list of reports into an Atom feed XML string (R20-safe).

    ``feed_name`` selects the logical feed (``immediate`` / ``digest``); it becomes
    part of the feed's urn id but is never a token. ``lang`` selects the single
    delivery language (entry bodies/titles + feed ``xml:lang``); a ``title`` of
    None is built localized from ``feed_name``.
    """
    fg = FeedGenerator()
    fg.id(f"{_FEED_ID_BASE}:{feed_name}")
    fg.title(title or delivery_strings(lang)["feed_title"].format(name=feed_name))
    fg.link(href=f"{_FEED_ID_BASE}:{feed_name}", rel="self")
    fg.language(HTML_LANG.get(lang, "en"))
    # feedgen requires at least one author at the feed level.
    fg.author({"name": "transmutary"})
    # Most-recent first.
    for report in reports:
        _render_entry(fg, report, lang)
    if not reports:
        # feedgen needs an updated timestamp; an empty feed still validates.
        fg.updated("1970-01-01T00:00:00+00:00")
    return fg.atom_str(pretty=True).decode("utf-8")


def render_single(
    report: Report,
    *,
    feed_name: str = DeliveryRoute.IMMEDIATE.value,
    lang: str = DEFAULT_LANG,
) -> str:
    """Render one report as a single-entry feed (the immediate-push case)."""
    return render_feed([report], feed_name=feed_name, lang=lang)
