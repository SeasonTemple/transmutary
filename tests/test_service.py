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
    effective_repos,
    reconcile_repo_jobs,
    register_repo_jobs,
    unregister_repo_jobs,
)
from transmutary.store.state import StateStore


def _settings(
    *, repos=("acme/cli", "acme/gateway"), email=False,
    security_interval=300, release_issue_interval=600,
) -> Settings:
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
            security_interval_seconds=security_interval,
            release_issue_interval_seconds=release_issue_interval,
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
    assert "daily-publish" in job_ids
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


def test_trend_cron_uses_effective_admin_digest_hour(store):
    settings = _settings(repos=("acme/cli",), email=True)
    store.set_admin_delivery_preferences(email_recipients=None, digest_hour=17)
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    trend = {j.id: j for j in sched.get_jobs()}["daily-publish"]
    assert str(trend.trigger.fields[5]) == "17"


# --- U3: daily-publish merges trend→digest into one ordered, isolated job -------
def test_daily_publish_is_single_job_no_separate_race(store):
    # Regression guard: the old design had TWO same-hour cron jobs ("trend" +
    # "daily-digest") that raced. They must be merged into one "daily-publish".
    settings = _settings(repos=("acme/cli",), email=True)
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    ids = {j.id for j in sched.get_jobs()}
    assert "daily-publish" in ids
    assert "trend" not in ids
    assert "daily-digest" not in ids


def test_daily_publish_runs_trend_then_digest(store, monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(service, "run_trend_tick", lambda *a, **k: order.append("trend"))
    monkeypatch.setattr(service, "run_daily_digest", lambda *a, **k: order.append("digest"))
    settings = _settings(repos=("acme/cli",), email=True)
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    {j.id: j for j in sched.get_jobs()}["daily-publish"].func()
    assert order == ["trend", "digest"]  # producer before consumer, guaranteed


def test_daily_publish_trend_failure_still_runs_digest(store, monkeypatch):
    order: list[str] = []

    def boom(*a, **k):
        order.append("trend")
        raise RuntimeError("trend down")

    monkeypatch.setattr(service, "run_trend_tick", boom)
    monkeypatch.setattr(service, "run_daily_digest", lambda *a, **k: order.append("digest"))
    settings = _settings(repos=("acme/cli",), email=True)
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    # Per-phase isolation: trend raising must NOT abort the digest phase.
    {j.id: j for j in sched.get_jobs()}["daily-publish"].func()
    assert order == ["trend", "digest"]


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
    assert "daily-publish" in job_ids
    assert "placeholder" not in job_ids


# ===========================================================================
# F4 — effective_repos, dynamic registration, reconcile, Service.promote/demote
# ===========================================================================
import pytest  # noqa: E402 - test-only import grouped with the F4 block


@dataclass
class _FakeRuntimeWithStore:
    """Fake runtime carrying a real in-memory StateStore for F4 reconcile tests.

    Only the attributes the registration / reconcile code reads are populated
    (settings, store, has_email_leg). The ticks themselves are never executed
    (scheduler is never started), so no network / LLM is involved."""

    settings: Settings
    store: StateStore
    has_email_leg: bool = True


@pytest.fixture
def store():
    s = StateStore(":memory:")
    yield s
    s.close()


def _rt_store(settings, store) -> _FakeRuntimeWithStore:
    return _FakeRuntimeWithStore(settings=settings, store=store)


def _repo_job_ids(sched):
    return {j.id for j in sched.get_jobs()}


# --- U2: effective_repos ---


def test_effective_repos_config_only(store):
    settings = _settings(repos=("acme/cli", "acme/gateway"))
    assert effective_repos(settings, store) == ["acme/cli", "acme/gateway"]


def test_effective_repos_union_dedup_sorted(store):
    settings = _settings(repos=("acme/cli", "acme/gateway"))
    store.promote_repo("z/hot")
    store.promote_repo("acme/cli")  # overlaps config → must not duplicate
    assert effective_repos(settings, store) == ["acme/cli", "acme/gateway", "z/hot"]


def test_effective_repos_none_store_degrades_to_config():
    settings = _settings(repos=("b/two", "a/one"))
    assert effective_repos(settings, None) == ["a/one", "b/two"]


# --- U3: boot over effective list ---


def test_boot_registers_promoted_repo_jobs(store):
    store.promote_repo("hot/candidate")
    settings = _settings(repos=("acme/cli",))
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    ids = _repo_job_ids(sched)
    assert "security:hot/candidate" in ids
    assert "release-issue:hot/candidate" in ids
    assert "security:acme/cli" in ids
    assert "reconcile" in ids


def test_boot_registers_admin_repo_jobs(store):
    store.add_admin_repo("admin/tracked")
    settings = _settings(repos=("acme/cli",))
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    ids = _repo_job_ids(sched)
    assert "security:admin/tracked" in ids
    assert "release-issue:admin/tracked" in ids
    assert "security:acme/cli" in ids


# --- U3: reconcile cross-process sync + misdeletion protection ---


def test_reconcile_picks_up_promote_from_another_process(store):
    settings = _settings(repos=("acme/cli",))
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    assert "security:later/promoted" not in _repo_job_ids(sched)
    # Simulate a CLI promote in a SEPARATE process writing the shared table.
    store.promote_repo("later/promoted")
    reconcile_repo_jobs(sched, _rt_store(settings, store))
    ids = _repo_job_ids(sched)
    assert "security:later/promoted" in ids
    assert "release-issue:later/promoted" in ids


def test_reconcile_picks_up_admin_repo_from_another_process(store):
    settings = _settings(repos=("acme/cli",))
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    assert "security:admin/later" not in _repo_job_ids(sched)

    store.add_admin_repo("admin/later")
    reconcile_repo_jobs(sched, _rt_store(settings, store))
    ids = _repo_job_ids(sched)
    assert "security:admin/later" in ids
    assert "release-issue:admin/later" in ids


def test_reconcile_removes_removed_admin_repo(store):
    settings = _settings(repos=("acme/cli",))
    store.add_admin_repo("admin/temp")
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    assert "security:admin/temp" in _repo_job_ids(sched)

    store.remove_admin_repo("admin/temp")
    reconcile_repo_jobs(sched, _rt_store(settings, store))
    ids = _repo_job_ids(sched)
    assert "security:admin/temp" not in ids
    assert "release-issue:admin/temp" not in ids


def test_reconcile_removes_demoted_repo(store):
    settings = _settings(repos=("acme/cli",))
    store.promote_repo("temp/repo")
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    assert "security:temp/repo" in _repo_job_ids(sched)
    store.demote_repo("temp/repo")
    reconcile_repo_jobs(sched, _rt_store(settings, store))
    ids = _repo_job_ids(sched)
    assert "security:temp/repo" not in ids
    assert "release-issue:temp/repo" not in ids


def test_reconcile_never_removes_config_repo_jobs(store):
    # Misdeletion guard: config repos are always in the effective list, so reconcile
    # must keep their jobs even with an empty promoted set.
    settings = _settings(repos=("acme/cli", "acme/gateway"))
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    reconcile_repo_jobs(sched, _rt_store(settings, store))  # nothing promoted
    ids = _repo_job_ids(sched)
    for repo in ("acme/cli", "acme/gateway"):
        assert f"security:{repo}" in ids
        assert f"release-issue:{repo}" in ids


def test_reconcile_leaves_non_repo_jobs_untouched(store):
    settings = _settings(repos=("acme/cli",))
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    reconcile_repo_jobs(sched, _rt_store(settings, store))
    ids = _repo_job_ids(sched)
    assert "daily-publish" in ids
    assert "reconcile" in ids


# --- U3: idempotency (no duplicate jobs) ---


@pytest.fixture
def paused_sched():
    # A started-but-paused scheduler: jobs land in the jobstore (so
    # replace_existing actually dedupes) but no job runs (no network/LLM).
    s = BackgroundScheduler()
    s.start(paused=True)
    yield s
    s.shutdown(wait=False)


def test_register_repo_jobs_idempotent_no_duplicates(store, paused_sched):
    settings = _settings(repos=("acme/cli",))
    rt = _rt_store(settings, store)
    register_repo_jobs(paused_sched, rt, "dup/repo")
    register_repo_jobs(paused_sched, rt, "dup/repo")  # repeat → replace_existing, no dup
    ids = [j.id for j in paused_sched.get_jobs()]
    assert ids.count("security:dup/repo") == 1
    assert ids.count("release-issue:dup/repo") == 1


def test_repeated_reconcile_no_duplicate_jobs(store, paused_sched):
    settings = _settings(repos=("acme/cli",))
    store.promote_repo("hot/one")
    rt = _rt_store(settings, store)
    build_scheduler(paused_sched, settings=settings, runtime=rt)
    for _ in range(3):
        reconcile_repo_jobs(paused_sched, rt)
    ids = [j.id for j in paused_sched.get_jobs()]
    assert ids.count("security:hot/one") == 1
    assert ids.count("release-issue:hot/one") == 1


# --- configurable poll intervals: registration + hot-reload (U4) ---

def _interval(sched, job_id):
    return sched.get_job(job_id).trigger.interval.total_seconds()


def test_register_uses_effective_intervals_from_yaml(paused_sched):
    # base yaml intervals (no store) flow into the registered jobs.
    settings = _settings(repos=("acme/cli",), security_interval=180, release_issue_interval=1800)
    build_scheduler(paused_sched, settings=settings, runtime=_fake_rt(settings))
    assert _interval(paused_sched, "security:acme/cli") == 180
    assert _interval(paused_sched, "release-issue:acme/cli") == 1800


def test_reconcile_reschedules_on_admin_interval_change(store, paused_sched):
    settings = _settings(repos=("acme/cli",))
    rt = _rt_store(settings, store)
    build_scheduler(paused_sched, settings=settings, runtime=rt)
    assert _interval(paused_sched, "security:acme/cli") == 300  # default
    # admin (WebUI) changes the cadence in a separate process
    store.set_admin_delivery_preferences(security_interval_seconds=240)
    reconcile_repo_jobs(paused_sched, rt)
    assert _interval(paused_sched, "security:acme/cli") == 240  # hot-reloaded


def test_reconcile_does_not_reschedule_when_interval_unchanged(store, paused_sched, monkeypatch):
    # Anti-starvation invariant: a long interval (> reconcile cadence) must NOT be
    # rescheduled every reconcile — that would reset next_run_time and starve it.
    settings = _settings(repos=("acme/cli",), security_interval=1800, release_issue_interval=1800)
    rt = _rt_store(settings, store)
    build_scheduler(paused_sched, settings=settings, runtime=rt)
    calls = {"n": 0}
    orig = paused_sched.reschedule_job

    def _counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(paused_sched, "reschedule_job", _counting)
    for _ in range(3):
        reconcile_repo_jobs(paused_sched, rt)
    assert calls["n"] == 0  # unchanged intervals → zero reschedules → no starvation
    assert _interval(paused_sched, "security:acme/cli") == 1800


def test_reconcile_reschedule_tolerates_missing_job(store, paused_sched):
    # Race: a repo present in the effective set but whose job vanished mid-cycle
    # must not raise.
    settings = _settings(repos=("acme/cli",))
    rt = _rt_store(settings, store)
    build_scheduler(paused_sched, settings=settings, runtime=rt)
    paused_sched.remove_job("security:acme/cli")
    store.set_admin_delivery_preferences(security_interval_seconds=240)
    reconcile_repo_jobs(paused_sched, rt)  # must not raise


# --- U3: unregister tolerance ---


def test_unregister_missing_repo_is_noop(store):
    sched = BackgroundScheduler()
    # No jobs registered for this repo → must not raise.
    unregister_repo_jobs(sched, "never/registered")


# --- U3: Service.promote / demote immediate effect ---


def test_service_promote_registers_immediately_and_persists(store):
    settings = _settings(repos=("acme/cli",))
    sched = BackgroundScheduler()
    svc = Service(sched, settings=settings, runtime=_rt_store(settings, store))
    svc.promote("fresh/repo")
    ids = _repo_job_ids(svc.scheduler)
    assert "security:fresh/repo" in ids  # immediate, not waiting for reconcile
    assert "release-issue:fresh/repo" in ids
    assert store.is_promoted("fresh/repo")  # persisted to shared table


def test_service_demote_unregisters_and_persists(store):
    settings = _settings(repos=("acme/cli",))
    store.promote_repo("temp/repo")
    sched = BackgroundScheduler()
    svc = Service(sched, settings=settings, runtime=_rt_store(settings, store))
    assert "security:temp/repo" in _repo_job_ids(svc.scheduler)
    svc.demote("temp/repo")
    ids = _repo_job_ids(svc.scheduler)
    assert "security:temp/repo" not in ids
    assert not store.is_promoted("temp/repo")


def test_service_without_settings_cannot_promote():
    # Backward compat: placeholder-only service has no runtime → promote raises.
    svc = Service(BackgroundScheduler())
    assert svc.runtime is None
    with pytest.raises(RuntimeError):
        svc.promote("x/y")


# --- U3: reconcile job carries KTD-F flags (anti-overlap) ---


def test_reconcile_job_has_max_instances_and_coalesce(store):
    settings = _settings(repos=("acme/cli",))
    sched = BackgroundScheduler()
    build_scheduler(sched, settings=settings, runtime=_rt_store(settings, store))
    by_id = {j.id: j for j in sched.get_jobs()}
    assert by_id["reconcile"].max_instances == 1
    assert by_id["reconcile"].coalesce is True


# --- Import-time isolation: scheduler entry points must not pull litellm -------
#
# Service / pipeline / filter are imported by the long-running scheduler.
# Loading litellm on the import path adds 2-3s to startup and a hard
# dependency on a network-touchy library, even when the process only does
# config parsing, reconcile, or dashboard reads. The LLM gateway is resolved
# at first tick, not at import.
def _import_isolated(import_fn):
    import sys

    for mod in list(sys.modules):
        if mod == "litellm" or mod.startswith("litellm."):
            del sys.modules[mod]
        if mod == "transmutary.llm":
            del sys.modules[mod]
    import_fn()
    return any(m == "litellm" or m.startswith("litellm.") for m in sys.modules)


def test_importing_service_does_not_load_litellm():
    assert not _import_isolated(lambda: __import__("transmutary.service"))
    # And transmutary.llm itself stays out of the chain.
    import sys

    assert "transmutary.llm" not in sys.modules


def test_importing_pipeline_does_not_load_litellm():
    assert not _import_isolated(lambda: __import__("transmutary.pipeline"))


def test_importing_filter_does_not_load_litellm():
    assert not _import_isolated(lambda: __import__("transmutary.filter"))


def test_importing_dashboard_app_does_not_load_litellm():
    # Dashboard is read-only at request time and never invokes the LLM.
    # Its import path must stay clean of the litellm bootstrap.
    assert not _import_isolated(lambda: __import__("transmutary.dashboard.app"))


def test_resolving_llm_error_still_works_after_lazy_refactor():
    # Sanity: the lazy LLMError import in filter must still be reachable
    # through the package surface, even though the module top-level
    # no longer imports it.
    from transmutary.filter import _judge, _parse_verdict, filter_issue_surge  # noqa: F401

    # The local imports inside _judge / _parse_verdict / filter_issue_surge
    # resolve at call time, not import time. Force one path to make sure
    # the import chain inside the function body still works.
    from transmutary.llm import LLMError as _LLMError  # noqa: F401

    assert _LLMError.__name__ == "LLMError"
