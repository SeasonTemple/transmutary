"""U15 email.py tests — SMTP send, env creds, failure raises (R21)."""

from __future__ import annotations

import pytest

from transmutary.deliver.email import EmailDeliveryError, send_report
from transmutary.report.schema import Report, ReportKind, Severity


def _report():
    return Report(
        kind=ReportKind.DIAGNOSE,
        repo="acme/cli",
        title="outage",
        body_md="body",
        severity=Severity.CRITICAL,
        created_at="2026-05-29T10:00:00+00:00",
    )


class _FakeSMTP:
    def __init__(self):
        self.events = []
        self.sent = None

    def starttls(self):
        self.events.append("starttls")

    def login(self, user, password):
        self.events.append(("login", user, password))

    def send_message(self, msg):
        self.sent = msg

    def quit(self):
        self.events.append("quit")


def test_send_success_uses_injected_creds():
    fake = _FakeSMTP()
    send_report(
        _report(), ["ops@example.com"],
        smtp_user="mailer@example.com", smtp_password="pw",
        host="smtp.example.com", smtp_factory=lambda: fake,
    )
    assert fake.sent is not None
    assert fake.sent["To"] == "ops@example.com"
    assert ("login", "mailer@example.com", "pw") in fake.events


def test_no_recipients_raises():
    with pytest.raises(EmailDeliveryError):
        send_report(_report(), [], smtp_user="u", smtp_password="p", host="h",
                    smtp_factory=lambda: _FakeSMTP())


def test_smtp_failure_raises_delivery_error_without_password_leak():
    class _Boom:
        def starttls(self):
            pass

        def login(self, u, p):
            raise RuntimeError("auth rejected for some-secret-pw context")

        def quit(self):
            pass

    with pytest.raises(EmailDeliveryError) as ei:
        send_report(_report(), ["ops@example.com"], smtp_user="u",
                    smtp_password="some-secret-pw", host="h",
                    smtp_factory=lambda: _Boom())
    # R21: the raised error must NOT contain the password value.
    assert "some-secret-pw" not in str(ei.value)
