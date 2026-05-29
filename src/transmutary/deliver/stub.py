"""Delivery routing + full RSS/email delivery (U4 signature, U15 body; R14/KTD1).

Routing is a two-branch INLINE condition (KTD1 — NO channel.py abstraction):
urgent (high-risk) → immediate path (instant RSS entry + email); else → digest
path (accumulated daily-digest feed). The ``deliver()`` signature is preserved
from the Phase 0 stub; U15 fills in the real RSS (``rss.py``) and email
(``email.py``) implementations behind it.

When no delivery sink is configured (the Phase 0 / F1-chain case), ``deliver()``
still renders the report to ``<artifact_root>/_delivered/<route>/`` or stdout and
records the chosen route — so the F1 diagnosis chain runs without standing up the
ASGI server or SMTP. Passing an :class:`OutboundDelivery` config activates the
real RSS feed write + email send (the two inline branches), with SMTP failure
degrading WITHOUT dropping the RSS leg.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field

from ..report.schema import Report, Severity
from . import email as email_mod
from . import rss as rss_mod


class DeliveryRoute(str, enum.Enum):
    IMMEDIATE = "immediate"  # high-risk → instant RSS + email (Phase 1)
    DIGEST = "digest"  # low-priority → daily digest


@dataclass
class OutboundDelivery:
    """Configuration for real outbound delivery (U15).

    Holds the non-secret connection settings plus credential VALUES that the
    caller has read from :class:`~transmutary.config.Credentials` (R21). Nothing
    here is persisted or logged. ``feed_dir`` is where the rendered Atom feed is
    written (private path, R24); the ASGI server (:mod:`server`) serves it.
    """

    feed_dir: str | None = None
    email_recipients: list[str] = field(default_factory=list)
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_factory: object | None = None  # test seam


@dataclass
class DeliveryResult:
    """Records which inline branch handled the report (asserted by tests)."""

    route: DeliveryRoute
    path: str | None  # file path if written, else None (stdout)
    report_title: str
    rss_path: str | None = None  # written Atom feed path (immediate/digest), if any
    email_sent: bool = False
    email_degraded: bool = False  # SMTP failed but RSS still delivered (no drop)
    email_error: str | None = None


def _route_for(report: Report, urgency: Severity | None) -> DeliveryRoute:
    """Inline two-branch routing (KTD1)."""
    severity = urgency if urgency is not None else report.severity
    return DeliveryRoute.IMMEDIATE if severity.is_urgent else DeliveryRoute.DIGEST


def deliver(
    report: Report,
    urgency: Severity | None = None,
    *,
    artifact_root: str | None = None,
    to_stdout: bool = False,
    outbound: OutboundDelivery | None = None,
) -> DeliveryResult:
    """Deliver a report via the inline two-branch route (KTD1).

    Args:
        report: the report to deliver.
        urgency: optional severity override for routing; defaults to report's own.
        artifact_root: if given, write the rendered report under
            ``<artifact_root>/_delivered/<route>/``; otherwise (or with
            ``to_stdout``) print to stdout.
        outbound: when provided, activates the real U15 delivery — an Atom feed
            entry for the route (immediate/digest) plus, on the IMMEDIATE branch,
            an email. SMTP failure degrades (recorded) WITHOUT dropping the RSS leg.

    Returns:
        DeliveryResult recording the chosen route (and file path if written).
    """
    route = _route_for(report, urgency)
    rendered = _render(report, route)

    result: DeliveryResult
    if to_stdout or artifact_root is None:
        print(rendered)
        result = DeliveryResult(route=route, path=None, report_title=report.title)
    else:
        out_dir = os.path.join(artifact_root, "_delivered", route.value)
        os.makedirs(out_dir, exist_ok=True)
        safe = report.repo.replace("/", "__").replace("\\", "__")
        path = os.path.join(out_dir, f"{safe}-{report.kind.value}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        result = DeliveryResult(route=route, path=path, report_title=report.title)

    if outbound is not None:
        _deliver_outbound(report, route, outbound, result)
    return result


def _deliver_outbound(
    report: Report,
    route: DeliveryRoute,
    outbound: OutboundDelivery,
    result: DeliveryResult,
) -> None:
    """Real U15 delivery: write the Atom feed; email on the immediate branch.

    Both branches write a feed (immediate → single-entry instant feed; digest →
    digest feed). The email leg runs ONLY on the immediate branch. R: an SMTP
    failure degrades the email leg and is recorded, but never aborts the RSS leg
    (no dropped report).
    """
    # RSS leg (both branches) — written first so it is never lost to an SMTP error.
    if outbound.feed_dir is not None:
        os.makedirs(outbound.feed_dir, exist_ok=True)
        xml = rss_mod.render_single(report, feed_name=route.value)
        feed_path = os.path.join(outbound.feed_dir, f"{route.value}.atom.xml")
        with open(feed_path, "w", encoding="utf-8") as fh:
            fh.write(xml)
        result.rss_path = feed_path

    # Email leg — immediate branch only, and only if recipients/SMTP configured.
    if route is DeliveryRoute.IMMEDIATE and outbound.email_recipients and outbound.smtp_user:
        try:
            email_mod.send_report(
                report,
                outbound.email_recipients,
                smtp_user=outbound.smtp_user,
                smtp_password=outbound.smtp_password or "",
                host=outbound.smtp_host or "localhost",
                port=outbound.smtp_port,
                use_tls=outbound.smtp_use_tls,
                smtp_factory=outbound.smtp_factory,
            )
            result.email_sent = True
        except email_mod.EmailDeliveryError as exc:
            # Degrade: record but do NOT drop the RSS delivery.
            result.email_degraded = True
            result.email_error = str(exc)


def _render(report: Report, route: DeliveryRoute) -> str:
    return (
        f"[{route.value.upper()}] {report.title}\n"
        f"repo={report.repo} severity={report.severity.value} kind={report.kind.value}\n\n"
        f"{report.body_md}\n"
    )
