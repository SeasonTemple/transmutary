"""U15 wired-delivery tests — inline two-branch route drives RSS + email (KTD1).

Covers: high-risk → immediate RSS entry + email; mode-B/low → digest feed only;
SMTP failure degrades without dropping RSS (no channel.py — KTD1).
"""

from __future__ import annotations

from transmutary.deliver.stub import DeliveryRoute, OutboundDelivery, deliver
from transmutary.report.schema import Report, ReportKind, Severity


def _report(severity, kind=ReportKind.DIAGNOSE):
    return Report(
        kind=kind,
        repo="acme/cli",
        title="t",
        body_md="body",
        severity=severity,
        created_at="2026-05-29T10:00:00+00:00",
    )


class _FakeSMTP:
    def __init__(self):
        self.sent = None

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        self.sent = msg

    def quit(self):
        pass


def _outbound(feed_dir, smtp_factory=None, recipients=("ops@example.com",)):
    return OutboundDelivery(
        feed_dir=str(feed_dir),
        email_recipients=list(recipients),
        smtp_user="mailer@example.com",
        smtp_password="pw",
        smtp_host="smtp.example.com",
        smtp_factory=smtp_factory,
    )


def test_high_risk_immediate_rss_plus_email(tmp_path):
    fake = _FakeSMTP()
    res = deliver(_report(Severity.CRITICAL), artifact_root=str(tmp_path),
                  outbound=_outbound(tmp_path / "feeds", smtp_factory=lambda: fake))
    assert res.route is DeliveryRoute.IMMEDIATE
    assert res.rss_path and res.rss_path.endswith("immediate.atom.xml")
    assert res.email_sent is True
    assert fake.sent is not None


def test_mode_b_low_priority_digest_feed_only(tmp_path):
    fake = _FakeSMTP()
    res = deliver(_report(Severity.NORMAL, kind=ReportKind.EXPLAIN),
                  artifact_root=str(tmp_path),
                  outbound=_outbound(tmp_path / "feeds", smtp_factory=lambda: fake))
    assert res.route is DeliveryRoute.DIGEST
    assert res.rss_path.endswith("digest.atom.xml")
    # Digest branch does not email.
    assert res.email_sent is False
    assert fake.sent is None


def test_supply_chain_high_risk_immediate_feed(tmp_path):
    # F3: a high-risk supply-chain report goes straight to the immediate feed.
    res = deliver(_report(Severity.CRITICAL), artifact_root=str(tmp_path),
                  outbound=_outbound(tmp_path / "feeds", recipients=()))
    assert res.route is DeliveryRoute.IMMEDIATE
    assert res.rss_path.endswith("immediate.atom.xml")


def test_smtp_failure_degrades_without_dropping_rss(tmp_path):
    class _Boom:
        def starttls(self):
            pass

        def login(self, u, p):
            raise RuntimeError("smtp down")

        def quit(self):
            pass

    res = deliver(_report(Severity.HIGH), artifact_root=str(tmp_path),
                  outbound=_outbound(tmp_path / "feeds", smtp_factory=lambda: _Boom()))
    # RSS still delivered despite SMTP failure (no drop).
    assert res.rss_path is not None
    assert res.email_sent is False
    assert res.email_degraded is True
    assert res.email_error is not None
