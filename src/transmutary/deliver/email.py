"""SMTP email delivery (U15, R14/R21, KTD4).

Sends a high-risk ``Report`` as an email. SMTP credentials come ONLY from env via
the :class:`~transmutary.config.Credentials` container (R21/KTD4) — they are never
logged, never embedded in the message, and never persisted.

Failure handling (R: SMTP failure degrades, never drops the RSS delivery): a send
failure raises :class:`EmailDeliveryError`, which the inline delivery router
catches and records as a degraded email leg WITHOUT failing the RSS leg.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from ..report.schema import Report


class EmailDeliveryError(Exception):
    """Raised when the SMTP send fails. Caught by the router (RSS still delivered)."""


def _build_message(report: Report, *, sender: str, recipients: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[transmutary/{report.severity.value}] {report.title}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    body = report.body_md
    if report.sources:
        body += "\n\nSources:\n" + "\n".join(
            f"- {s.source_id}: {s.url} (fetched {s.fetched_at})" for s in report.sources
        )
    msg.set_content(body)
    return msg


def send_report(
    report: Report,
    recipients: list[str],
    *,
    smtp_user: str,
    smtp_password: str,
    host: str,
    port: int = 587,
    use_tls: bool = True,
    smtp_factory=None,
) -> None:
    """Send ``report`` to ``recipients`` over SMTP.

    Args:
        smtp_user / smtp_password: credentials sourced from env (R21). Passed in by
            the caller (which reads them from ``Credentials``); never read from
            disk here, never logged.
        host / port / use_tls: SMTP server connection settings (non-secret config).
        smtp_factory: test seam returning an object with the smtplib.SMTP API.

    Raises:
        EmailDeliveryError: on any SMTP failure (the router degrades, keeping RSS).
    """
    if not recipients:
        raise EmailDeliveryError("no recipients configured")
    msg = _build_message(report, sender=smtp_user, recipients=recipients)
    factory = smtp_factory if smtp_factory is not None else (lambda: smtplib.SMTP(host, port))
    try:
        smtp = factory()
        try:
            if use_tls:
                smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001 - quit failure must not mask send result
                pass
    except EmailDeliveryError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize all SMTP failures
        # NOTE: str(exc) may echo server text but NEVER the password — the password
        # is not interpolated into any message here (R21/KTD4).
        raise EmailDeliveryError(f"SMTP send failed: {type(exc).__name__}") from exc
