"""U15 email.py tests — SMTP send, env creds, failure raises (R21)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import pytest

from transmutary.deliver.email import EmailDeliveryError, _send_message, send_report
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


# --- SMTP retry (round-robin null-routed IP dodge) ---------------------------
def _msg() -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = "x"
    m["From"] = "a@b.com"
    m["To"] = "c@d.com"
    m.set_content("hi")
    return m


def test_retry_succeeds_on_second_attempt_with_fresh_connection():
    # First connection lands on a null-routed IP (TimeoutError at connect); the
    # retry opens a FRESH connection (new DNS resolve) and succeeds.
    calls = {"n": 0}
    sleeps = []

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("connection to blocked IP timed out")
        return _FakeSMTP()

    _send_message(
        _msg(), smtp_user="u", smtp_password="p", host="smtp.gmail.com", port=465,
        use_tls=False, use_ssl=True, smtp_factory=factory, sleep_fn=sleeps.append,
    )
    assert calls["n"] == 2  # retried once
    assert sleeps == [pytest.approx(2.0)]  # one backoff before the retry


def test_retry_exhausts_then_raises_without_password_leak():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        raise TimeoutError("blocked; password=some-secret-pw in transport log")

    with pytest.raises(EmailDeliveryError) as ei:
        _send_message(
            _msg(), smtp_user="u", smtp_password="some-secret-pw", host="h", port=465,
            use_tls=False, use_ssl=True, smtp_factory=factory,
            max_attempts=3, sleep_fn=lambda _s: None,
        )
    assert calls["n"] == 3  # all attempts used
    assert "after 3 attempts" in str(ei.value)
    assert "some-secret-pw" not in str(ei.value)  # R21: type name only, no server text


def test_permanent_auth_error_not_retried():
    # Bad credentials are permanent — retrying cannot help, so fail on attempt 1.
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        fake = _FakeSMTP()
        fake.login = lambda u, p: (_ for _ in ()).throw(
            smtplib.SMTPAuthenticationError(535, b"bad creds")
        )
        return fake

    with pytest.raises(EmailDeliveryError):
        _send_message(
            _msg(), smtp_user="u", smtp_password="p", host="h", port=465,
            use_tls=False, use_ssl=True, smtp_factory=factory, sleep_fn=lambda _s: None,
        )
    assert calls["n"] == 1  # NOT retried
