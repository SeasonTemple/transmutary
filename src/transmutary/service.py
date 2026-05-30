"""Service entry point + scheduler bootstrap (U5 placeholder / U6 real wiring, R19).

Two modes, selected by whether ``settings`` is supplied (KTD-C backward compat):

  * ``build_scheduler(scheduler)`` / ``settings=None`` — the Phase 0 placeholder
    heartbeat job, so the existing ``test_service`` suite keeps passing and a
    scheduler can still be booted without config.
  * ``build_scheduler(settings=<Settings>, creds=<Credentials>)`` — the real
    tiered jobs (U6): a security tick (minutes, KTD-B), a release/issue tick per
    watchlist repo (~10 min), and a daily trend tick (cron at
    ``delivery.digest_hour``). Each is wrapped in ``_isolated`` (one bad run is
    caught + logged, never kills the scheduler — R19) and registered with
    ``max_instances=1`` + ``coalesce=True`` (KTD-F: a job slower than its interval
    must not overlap itself and race the shared baseline/since cursor).

The shared :class:`~transmutary.pipeline.PipelineRuntime` (one StateStore, one
SSRF-safe client, one OutboundDelivery — KTD-D/KTD-E) is built once and closed
over by every job. When the outbound config has no email leg, a single warning is
logged at registration time so an RSS-only deployment's black-hole email leg is
visible (KTD-D), without failing the boot (RSS is the primary channel, R14).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from .config import Credentials, Settings
from .pipeline import (
    PipelineRuntime,
    build_runtime,
    run_release_issue_tick,
    run_security_tick,
    run_trend_tick,
)
from .store.state import StateStore

logger = logging.getLogger("transmutary.service")

# Phase 0 placeholder cadence (seconds), used when no Settings is supplied.
PLACEHOLDER_INTERVAL_SECONDS = 60

# Real tiered cadences (KTD-B — module constants, NOT config schema). The high-risk
# supply-chain source runs minute-level; release/issue runs ~10 min. The trend tick
# is daily (cron at delivery.digest_hour).
SECURITY_INTERVAL_SECONDS = 300
RELEASE_ISSUE_INTERVAL_SECONDS = 600

# F4 (KTD-B): how often the resident service reconciles its registered per-repo
# jobs against the effective watchlist (config ∪ promoted). This is the bridge
# that lets a CLI ``promote`` in a SEPARATE process reach the live scheduler
# without a restart.
RECONCILE_INTERVAL_SECONDS = 60


def effective_repos(settings: Settings, store: StateStore | None) -> list[str]:
    """The single source of truth for the observed repo set (F4, KTD-D).

    The effective watchlist = config ``watchlist`` repos ∪ promoted repos, with
    duplicates removed and a deterministic (sorted) order so registration and
    reconcile never diverge. ``store=None`` (no state store available, e.g. a fake
    runtime in a unit test) degrades to config-only, preserving backward compat.
    """
    repos = set(settings.watchlist.repo_names())
    if store is not None:
        repos.update(store.list_promoted())
    return sorted(repos)


def _isolated(job_id: str, func: Callable[[], None]) -> Callable[[], None]:
    """Wrap a job so an exception is caught, logged, and swallowed (isolation)."""

    def runner() -> None:
        try:
            func()
        except Exception:  # noqa: BLE001 - one bad job must not kill the scheduler
            logger.exception("scheduled job %r failed; isolating, will retry next run", job_id)

    return runner


def _placeholder_job() -> None:
    """No-op job confirming the scheduler is alive (Phase 0)."""
    logger.debug("transmutary placeholder heartbeat")


def build_scheduler(
    scheduler: BackgroundScheduler | None = None,
    *,
    settings: Settings | None = None,
    creds: Credentials | None = None,
    interval_seconds: int = PLACEHOLDER_INTERVAL_SECONDS,
    runtime: PipelineRuntime | None = None,
) -> BackgroundScheduler:
    """Build a scheduler and register jobs.

    Backward compatible (KTD-C): the first positional parameter is still
    ``scheduler``, and with ``settings=None`` only the Phase 0 placeholder job is
    registered. When ``settings`` is given, the real tiered pipeline jobs are
    registered instead (the placeholder is not). ``runtime`` is an injectable test
    seam — when omitted a real :class:`PipelineRuntime` is built from settings.
    """
    scheduler = scheduler if scheduler is not None else BackgroundScheduler()
    if settings is None:
        register_jobs(scheduler, interval_seconds=interval_seconds)
    else:
        rt = runtime if runtime is not None else build_runtime(settings, creds)
        register_pipeline_jobs(scheduler, rt)
    return scheduler


def register_jobs(
    scheduler: BackgroundScheduler, *, interval_seconds: int = PLACEHOLDER_INTERVAL_SECONDS
) -> None:
    """Register the Phase 0 placeholder job with isolation wrapping (KTD-C)."""
    scheduler.add_job(
        _isolated("placeholder", _placeholder_job),
        trigger="interval",
        seconds=interval_seconds,
        id="placeholder",
        replace_existing=True,
    )


def _repo_job_ids(repo: str) -> tuple[str, str]:
    """The two per-repo job ids (security + release/issue) for ``repo`` (F4)."""
    return (f"security:{repo}", f"release-issue:{repo}")


def register_repo_jobs(
    scheduler: BackgroundScheduler,
    runtime: PipelineRuntime,
    repo: str,
    *,
    security_interval: int = SECURITY_INTERVAL_SECONDS,
    release_issue_interval: int = RELEASE_ISSUE_INTERVAL_SECONDS,
) -> None:
    """Register the per-repo security + release/issue jobs for ``repo`` (F4).

    Idempotent: ``replace_existing=True`` means calling this again for a repo that
    is already registered replaces the jobs in place rather than producing
    duplicates (used by reconcile and by ``Service.promote`` immediate
    registration). Both jobs are isolated (R19) and carry ``max_instances=1`` +
    ``coalesce=True`` (KTD-F), matching the model used at boot — no sweep refactor.
    """
    scheduler.add_job(
        _isolated(
            f"security:{repo}",
            lambda repo=repo: run_security_tick(runtime, repo),
        ),
        trigger="interval",
        seconds=security_interval,
        id=f"security:{repo}",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _isolated(
            f"release-issue:{repo}",
            lambda repo=repo: run_release_issue_tick(runtime, repo),
        ),
        trigger="interval",
        seconds=release_issue_interval,
        id=f"release-issue:{repo}",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )


def unregister_repo_jobs(scheduler: BackgroundScheduler, repo: str) -> None:
    """Remove the per-repo jobs for ``repo`` (F4 demote / reconcile prune).

    Tolerant of missing jobs so a demote of a repo whose jobs were never
    registered (or already removed) does not raise.
    """
    for job_id in _repo_job_ids(repo):
        try:
            scheduler.remove_job(job_id)
        except Exception:  # noqa: BLE001 - job may already be gone; removal is best-effort
            logger.debug("job %r not present at unregister (already removed)", job_id)


def reconcile_repo_jobs(scheduler: BackgroundScheduler, runtime: PipelineRuntime) -> None:
    """Full-sync the registered per-repo jobs to the effective watchlist (F4, KTD-B).

    Reads the effective watchlist (config ∪ promoted) and the scheduler's currently
    registered per-repo jobs, then:

      * registers jobs for repos in the effective list that have none (picks up a
        promote done by a SEPARATE CLI process — the cross-process bridge), and
      * removes per-repo jobs whose repo is NO LONGER in the effective list
        (a demote). Crucially this only ever removes repos absent from the
        effective list, which always contains the config watchlist — so config
        repos' jobs are never collateral-removed. Non per-repo jobs (trend,
        reconcile, placeholder) are never touched.
    """
    store = getattr(runtime, "store", None)
    desired = set(effective_repos(runtime.settings, store))

    registered: set[str] = set()
    for job in scheduler.get_jobs():
        for prefix in ("security:", "release-issue:"):
            if job.id.startswith(prefix):
                registered.add(job.id[len(prefix):])

    for repo in desired - registered:
        register_repo_jobs(scheduler, runtime, repo)
    for repo in registered - desired:
        unregister_repo_jobs(scheduler, repo)


def register_pipeline_jobs(
    scheduler: BackgroundScheduler,
    runtime: PipelineRuntime,
    *,
    security_interval: int = SECURITY_INTERVAL_SECONDS,
    release_issue_interval: int = RELEASE_ISSUE_INTERVAL_SECONDS,
    reconcile_interval: int = RECONCILE_INTERVAL_SECONDS,
) -> None:
    """Register the real tiered pipeline jobs onto ``scheduler`` (U6 + F4).

    One security tick + one release/issue tick PER effective repo (config ∪
    promoted — F4 KTD-D) + one daily trend tick + one periodic reconcile job
    (KTD-B). All real jobs are isolated (R19) and set ``max_instances=1`` +
    ``coalesce=True`` (KTD-F). Before registering the high-risk security /
    release-issue jobs, a single warning is logged when the outbound config has no
    email leg (KTD-D — black-hole email leg made visible).
    """
    settings = runtime.settings
    store = getattr(runtime, "store", None)
    repos = effective_repos(settings, store)

    # KTD-D: high-risk alerts go out via RSS only when no email leg is configured.
    # Surface it ONCE at registration so the operator knows (RSS-only is legal).
    if not runtime.has_email_leg:
        logger.warning(
            "outbound email leg is NOT configured (no email_recipients/smtp_host); "
            "high-risk alerts will be delivered via RSS ONLY (KTD-D)"
        )

    # --- security + release/issue: one pair per effective repo (full coverage) ---
    for repo in repos:
        register_repo_jobs(
            scheduler,
            runtime,
            repo,
            security_interval=security_interval,
            release_issue_interval=release_issue_interval,
        )

    # --- trend: daily digest tick (cron at delivery.digest_hour) ---
    scheduler.add_job(
        _isolated("trend", lambda: run_trend_tick(runtime, ts=time.time())),
        trigger="cron",
        hour=settings.delivery.digest_hour,
        id="trend",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # --- reconcile: periodic full-sync so a CLI promote in another process is
    #     picked up without a restart, and a demote prunes stale jobs (F4 KTD-B).
    scheduler.add_job(
        _isolated("reconcile", lambda: reconcile_repo_jobs(scheduler, runtime)),
        trigger="interval",
        seconds=reconcile_interval,
        id="reconcile",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )


class Service:
    """Resident service with graceful start/stop.

    ``settings``/``creds`` are passed through to :func:`build_scheduler` (KTD-C):
    with neither, the Phase 0 placeholder runs; with ``settings`` the real tiered
    pipeline jobs are registered. When ``settings`` is given the shared
    :class:`PipelineRuntime` is retained so :meth:`promote` / :meth:`demote` can
    register/unregister per-repo jobs in-process (F4 immediate effect).
    """

    def __init__(
        self,
        scheduler: BackgroundScheduler | None = None,
        *,
        settings: Settings | None = None,
        creds: Credentials | None = None,
        runtime: PipelineRuntime | None = None,
    ) -> None:
        self.runtime: PipelineRuntime | None = None
        if settings is not None:
            # Build (or accept an injected) runtime once and reuse it for both the
            # scheduled jobs and in-process promote/demote, so all share one store.
            self.runtime = runtime if runtime is not None else build_runtime(settings, creds)
        self.scheduler = build_scheduler(
            scheduler, settings=settings, creds=creds, runtime=self.runtime
        )

    def promote(self, repo: str, source: str = "mode-b") -> None:
        """Promote ``repo``: persist it AND register its jobs immediately (F4).

        The persist makes it durable + visible to other processes (reconcile);
        the immediate registration means the live scheduler observes it now
        without waiting for the next reconcile tick. Requires a runtime (real
        settings); a placeholder-only service cannot promote.
        """
        if self.runtime is None:
            raise RuntimeError("promote requires a Service built with settings (no runtime)")
        self.runtime.store.promote_repo(repo, source=source)
        register_repo_jobs(self.scheduler, self.runtime, repo)

    def demote(self, repo: str) -> None:
        """Demote ``repo``: remove it from the promoted set AND unregister jobs (F4).

        Config watchlist repos are not affected by the store delete; but to keep
        immediate-effect symmetric with promote, the per-repo jobs are removed.
        The next reconcile re-derives the effective list as the source of truth.
        """
        if self.runtime is None:
            raise RuntimeError("demote requires a Service built with settings (no runtime)")
        self.runtime.store.demote_repo(repo)
        unregister_repo_jobs(self.scheduler, repo)

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
        job_ids = [j.id for j in self.scheduler.get_jobs()]
        logger.info("transmutary service started; jobs: %s", job_ids)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        logger.info("transmutary service stopped")


def main() -> None:  # pragma: no cover - real entrypoint, needs config + credentials
    import os

    from .config import load_settings

    logging.basicConfig(level=logging.INFO)
    config_dir = os.environ.get("TRANSMUTARY_CONFIG_DIR", "config")
    settings = load_settings(config_dir)
    service = Service(settings=settings, creds=settings.credentials)
    service.start()
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        service.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
