"""Phase 0 delivery stub (U4, R14/KTD1).

Minimal delivery that unblocks the Phase 1 F1 chain without the full RSS/email
implementation. Routing is a two-branch inline condition (KTD1 — NO channel.py
abstraction): urgent (high-risk) → immediate path; else → digest path. U15 will
replace the stub body while keeping the ``deliver`` signature.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass

from ..report.schema import Report, Severity


class DeliveryRoute(str, enum.Enum):
    IMMEDIATE = "immediate"  # high-risk → instant RSS + email (Phase 1)
    DIGEST = "digest"  # low-priority → daily digest


@dataclass
class DeliveryResult:
    """Records which inline branch handled the report (asserted by tests)."""

    route: DeliveryRoute
    path: str | None  # file path if written, else None (stdout)
    report_title: str


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
) -> DeliveryResult:
    """Deliver a report via the stub.

    Args:
        report: the report to deliver.
        urgency: optional severity override for routing; defaults to report's own.
        artifact_root: if given, write the rendered report under
            ``<artifact_root>/_delivered/<route>/``; otherwise (or with
            ``to_stdout``) print to stdout.

    Returns:
        DeliveryResult recording the chosen route (and file path if written).
    """
    route = _route_for(report, urgency)
    rendered = _render(report, route)

    if to_stdout or artifact_root is None:
        print(rendered)
        return DeliveryResult(route=route, path=None, report_title=report.title)

    out_dir = os.path.join(artifact_root, "_delivered", route.value)
    os.makedirs(out_dir, exist_ok=True)
    safe = report.repo.replace("/", "__").replace("\\", "__")
    path = os.path.join(out_dir, f"{safe}-{report.kind.value}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    return DeliveryResult(route=route, path=path, report_title=report.title)


def _render(report: Report, route: DeliveryRoute) -> str:
    return (
        f"[{route.value.upper()}] {report.title}\n"
        f"repo={report.repo} severity={report.severity.value} kind={report.kind.value}\n\n"
        f"{report.body_md}\n"
    )
