"""U4 deliver stub tests — two-branch routing only (KTD1)."""

from __future__ import annotations

import os

from transmutary.deliver.stub import DeliveryRoute, deliver
from transmutary.report.schema import Report, ReportKind, Severity


def _report(severity):
    return Report(
        kind=ReportKind.DIAGNOSE,
        repo="owner/name",
        title="t",
        body_md="body",
        severity=severity,
        created_at="2026-05-29T10:00:00Z",
    )


def test_high_risk_takes_immediate_path(tmp_path):
    res = deliver(_report(Severity.CRITICAL), artifact_root=str(tmp_path))
    assert res.route is DeliveryRoute.IMMEDIATE
    assert os.path.exists(res.path)
    assert "_delivered/immediate" in res.path


def test_low_priority_takes_digest_path(tmp_path):
    res = deliver(_report(Severity.NORMAL), artifact_root=str(tmp_path))
    assert res.route is DeliveryRoute.DIGEST
    assert "_delivered/digest" in res.path


def test_urgency_override(tmp_path):
    # report is NORMAL but caller forces HIGH urgency → immediate
    res = deliver(_report(Severity.NORMAL), urgency=Severity.HIGH, artifact_root=str(tmp_path))
    assert res.route is DeliveryRoute.IMMEDIATE


def test_stdout_path_records_route(capsys):
    res = deliver(_report(Severity.HIGH), to_stdout=True)
    assert res.route is DeliveryRoute.IMMEDIATE
    assert res.path is None
    out = capsys.readouterr().out
    assert "IMMEDIATE" in out
