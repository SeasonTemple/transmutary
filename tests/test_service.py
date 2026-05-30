"""U5/U6 service tests — placeholder backward-compat, real tiered job registration,
fault isolation, KTD-F anti-overlap, and the KTD-D black-hole email warning.

The scheduler is never STARTED (no real APScheduler threads); jobs are only
registered and inspected, so there is no real network / LLM. The pipeline runtime
is a lightweight fake — the ticks are never executed here (they have their own
mocked tests in test_pipeline.py)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apscheduler.schedulers.background import BackgroundScheduler

from transmutary import service
from transmutary.config import (
    Delivery,
    DependencyEdge,
    RepoEntry,
    Settings,
    TrendScope,
    Watchlist,
)
from transmutary.service import (
    RELEASE_ISSUE_INTERVAL_SECONDS,
    SECURITY_INTERVAL_SECONDS,
    Service,
    _isolated,
    build_scheduler,
)


def _settings(*, repos=("acme/cli", "acme/gateway"), email=False) -> Settings:
    return Settings(
        watchlist=Watchlist(
            repos=[RepoEntry(repo=r) for r in repos],
            dependency_edges=[DependencyEdge(from_repo="acme/cli", to_repo="acme/gateway")],
        ),
        trend_scope=TrendScope(topics=["ai"], keywords=["llm"]),
        delivery=Delivery(
            state_db_path=":memory:",
            artifact_root="/tmp/x",
            token_max_age_days=90,
            digest_hour=9,
            email_recipients=["a@example.com"] if email else [],
            smtp_host="smtp.example.com" if email else None,
        ),
    )


@dataclass
class _FakeRuntime:
    """Stand-in for PipelineRuntime: registration only reads settings + has_email_leg."""

    settings: Settings
    has_email_leg: bool


def test_build_scheduler_registers_placeholder():
    sched = BackgroundScheduler()
    build_scheduler(sched)
    job_ids = [j.id for j in sched.get_jobs()]
    assert "placeholder" in job_ids


def test_service_boots_and_stops():
    sched = BackgroundScheduler()
    svc = Service(sched)
    svc.start()
    try:
        assert svc.scheduler.running
        assert any(j.id == "placeholder" for j in svc.scheduler.get_jobs())
    finally:
        svc.stop()
    assert not svc.scheduler.running


def test_job_exception_is_isolated(caplog):
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("kaboom")

    wrapped = _isolated("boomer", boom)
    # Must NOT raise — the scheduler stays alive.
    wrapped()
    wrapped()
    assert calls["n"] == 2  # ran both times despite raising


def test_placeholder_job_is_callable():
    # the wrapped placeholder runs without raising
    service._isolated("placeholder", service._placeholder_job)()


# ===========================================================================
# U6 — real tiered job registration
# ===========================================================================
def _fake_rt(settings, *, email=False) -> _FakeRuntime:
    return _FakeRuntime(settings=settings, has_email_leg=email)


def test_build_scheduler_with_settings_registers_tiered_jobs():
    sched = BackgroundScheduler()
    settings = _settings(email=True)
    build_scheduler(sched, settings=settings, runtime=_fake_rt(settings, email=True))
    job_ids = {j.id for j in sched.get_jobs()}
    # placeholder is NOT registered when settings are supplied.
    assert "placeholder" not in job_ids
    # one trend job + per-repo security + per-repo release-issue jobs.
    assert "trend" in job_ids
    assert "security:acme/cli" in job_ids
    assert "security:acme/gateway" in job_ids
    assert "release-issue:acme/cli" in job_ids
    assert "release-issue:acme/gateway" in job_ids


def test_real_jobs_set_max_instances_and_coalesce():
    sched = BackgroundScheduler()
    settings = _settings(email=True)
    build_scheduler(sched, settings=settings, runtime=_fake_rt(settings, email=True))
    for job in sched.get_jobs():
        assert job.max_instances == 1, f"{job.id} must be max_instances=1 (KTD-F)"
        assert job.coalesce is True, f"{job.id} must coalesce=True (KTD-F)"


def test_security_and_release_intervals_match_constants():
    sched = BackgroundScheduler()
    settings = _settings(repos=("acme/cli",), email=True)
    build_scheduler(sched, settings=settings, runtime=_fake_rt(settings, email=True))
    by_id = {j.id: j for j in sched.get_jobs()}
    assert by_id["security:acme/cli"].trigger.interval.total_seconds() == SECURITY_INTERVAL_SECONDS
    rel = by_id["release-issue:acme/cli"].trigger.interval.total_seconds()
    assert rel == RELEASE_ISSUE_INTERVAL_SECONDS


def test_backward_compat_placeholder_when_no_settings():
    # Regression guard (KTD-C): no settings → still only the placeholder job, with
    # scheduler as the first positional arg.
    sched = BackgroundScheduler()
    build_scheduler(sched)
    job_ids = {j.id for j in sched.get_jobs()}
    assert job_ids == {"placeholder"}


def test_no_email_config_logs_black_hole_warning_once(caplog):
    sched = BackgroundScheduler()
    settings = _settings(email=False)
    with caplog.at_level(logging.WARNING, logger="transmutary.service"):
        build_scheduler(sched, settings=settings, runtime=_fake_rt(settings, email=False))
    warnings = [r for r in caplog.records if "RSS ONLY" in r.message]
    assert len(warnings) == 1  # exactly once (KTD-D)


def test_email_configured_does_not_warn(caplog):
    sched = BackgroundScheduler()
    settings = _settings(email=True)
    with caplog.at_level(logging.WARNING, logger="transmutary.service"):
        build_scheduler(sched, settings=settings, runtime=_fake_rt(settings, email=True))
    assert not [r for r in caplog.records if "RSS ONLY" in r.message]


def test_real_job_exception_is_isolated():
    # A tick raising must be swallowed by _isolated (R19) — scheduler stays alive.
    def boom():
        raise RuntimeError("tick blew up")

    wrapped = service._isolated("security:acme/cli", boom)
    wrapped()  # must NOT raise


def test_watchlist_multi_repo_full_coverage():
    sched = BackgroundScheduler()
    settings = _settings(repos=("a/one", "b/two", "c/three"), email=True)
    build_scheduler(sched, settings=settings, runtime=_fake_rt(settings, email=True))
    job_ids = {j.id for j in sched.get_jobs()}
    for repo in ("a/one", "b/two", "c/three"):
        assert f"security:{repo}" in job_ids
        assert f"release-issue:{repo}" in job_ids


def test_service_init_passes_settings_through():
    sched = BackgroundScheduler()
    settings = _settings(email=True)
    # Service must thread settings/creds into build_scheduler; inject runtime via a
    # build_scheduler call is not exposed, so verify through a real (in-memory) run.
    svc = Service(sched, settings=settings, creds=None)
    job_ids = {j.id for j in svc.scheduler.get_jobs()}
    assert "trend" in job_ids
    assert "placeholder" not in job_ids
