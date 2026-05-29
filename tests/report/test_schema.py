"""U1 Report schema tests — construction, round-trip, fixed sources section."""

from __future__ import annotations

from transmutary.report.schema import Report, ReportKind, Severity, Source


def _sample() -> Report:
    return Report(
        kind=ReportKind.DIAGNOSE,
        repo="example-org/upstream-cli",
        title="upstream-cli outage suspected source",
        body_md="Body **markdown** here.",
        severity=Severity.CRITICAL,
        created_at="2026-05-29T10:00:00Z",
        sources=[
            Source(source_id="gh-issue-1", url="https://github.com/x/y/issues/1",
                   fetched_at="2026-05-29T09:59:00Z"),
        ],
    )


def test_report_roundtrip():
    r = _sample()
    d = r.to_dict()
    back = Report.from_dict(d)
    assert back.kind is ReportKind.DIAGNOSE
    assert back.severity is Severity.CRITICAL
    assert back.repo == r.repo
    assert back.sources[0].source_id == "gh-issue-1"
    assert back.to_dict() == d


def test_sources_section_structure_fixed():
    r = _sample()
    d = r.to_dict()
    assert list(d["sources"][0].keys()) == ["source_id", "url", "fetched_at"]


def test_severity_urgency_routing():
    assert Severity.CRITICAL.is_urgent
    assert Severity.HIGH.is_urgent
    assert not Severity.NORMAL.is_urgent
    assert not Severity.INFO.is_urgent


def test_empty_sources_roundtrip():
    r = _sample()
    r.sources = []
    assert Report.from_dict(r.to_dict()).sources == []


def test_schema_is_stdlib_only():
    # KTD1: schema module must not import non-stdlib packages.
    import transmutary.report.schema as mod

    src = open(mod.__file__, encoding="utf-8").read()
    forbidden_imports = (
        "import yaml",
        "import httpx",
        "import litellm",
        "from ..",
        "from transmutary",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in src
