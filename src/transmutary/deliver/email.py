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
from .render_email import render_email_html, render_email_text


class EmailDeliveryError(Exception):
    """Raised when the SMTP send fails. Caught by the router (RSS still delivered)."""


def _build_message(
    report: Report, *, sender: str, recipients: list[str], lang: str = "en"
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[transmutary/{report.severity.value}] {report.title}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    # multipart/alternative (R5): plain-text fallback + rendered HTML body.
    msg.set_content(render_email_text(report, lang=lang))
    msg.add_alternative(render_email_html(report, lang=lang), subtype="html")
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
    use_ssl: bool = False,
    smtp_factory=None,
    lang: str = "en",
) -> None:
    """Send ``report`` to ``recipients`` over SMTP.

    Two connection modes (R14/KTD-D):
      * ``use_ssl=True`` → implicit TLS via ``SMTP_SSL`` (port 465).
      * else ``use_tls=True`` → STARTTLS upgrade on a plain ``SMTP`` (port 587).
    Gmail app passwords are shown in 4-space-separated groups; we strip
    whitespace so a pasted "xxxx xxxx xxxx xxxx" still authenticates.

    Args:
        smtp_user / smtp_password: credentials sourced from env (R21). Passed in by
            the caller (which reads them from ``Credentials``); never read from
            disk here, never logged.
        host / port / use_tls / use_ssl: SMTP connection settings (non-secret).
        smtp_factory: test seam returning an object with the smtplib.SMTP API.

    Raises:
        EmailDeliveryError: on any SMTP failure (the router degrades, keeping RSS).
    """
    if not recipients:
        raise EmailDeliveryError("no recipients configured")
    msg = _build_message(report, sender=smtp_user, recipients=recipients, lang=lang)
    _send_message(
        msg, smtp_user=smtp_user, smtp_password=smtp_password, host=host,
        port=port, use_tls=use_tls, use_ssl=use_ssl, smtp_factory=smtp_factory,
    )


def send_html(
    *,
    subject: str,
    text_body: str,
    html_body: str,
    recipients: list[str],
    smtp_user: str,
    smtp_password: str,
    host: str,
    port: int = 587,
    use_tls: bool = True,
    use_ssl: bool = False,
    smtp_factory=None,
) -> None:
    """Send an arbitrary multipart/alternative email (used by the daily digest).

    Same SMTP/credential contract as :func:`send_report`; the body is a pre-rendered
    (subject, text, html) triple rather than a single Report.
    """
    if not recipients:
        raise EmailDeliveryError("no recipients configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    _send_message(
        msg, smtp_user=smtp_user, smtp_password=smtp_password, host=host,
        port=port, use_tls=use_tls, use_ssl=use_ssl, smtp_factory=smtp_factory,
    )


def _send_message(
    msg: EmailMessage,
    *,
    smtp_user: str,
    smtp_password: str,
    host: str,
    port: int,
    use_tls: bool,
    use_ssl: bool,
    smtp_factory,
) -> None:
    """Low-level SMTP send (shared by send_report / send_html). Strips app-pw spaces."""
    smtp_password = smtp_password.replace(" ", "")  # Gmail app-password groups
    if smtp_factory is not None:
        factory = smtp_factory
    elif use_ssl:
        factory = lambda: smtplib.SMTP_SSL(host, port)  # noqa: E731
    else:
        factory = lambda: smtplib.SMTP(host, port)  # noqa: E731
    try:
        smtp = factory()
        try:
            if use_tls and not use_ssl:
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
