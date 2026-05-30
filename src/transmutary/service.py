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

logger = logging.getLogger("transmutary.service")

# Phase 0 placeholder cadence (seconds), used when no Settings is supplied.
PLACEHOLDER_INTERVAL_SECONDS = 60

# Real tiered cadences (KTD-B — module constants, NOT config schema). The high-risk
# supply-chain source runs minute-level; release/issue runs ~10 min. The trend tick
# is daily (cron at delivery.digest_hour).
SECURITY_INTERVAL_SECONDS = 300
RELEASE_ISSUE_INTERVAL_SECONDS = 600


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


def register_pipeline_jobs(
    scheduler: BackgroundScheduler,
    runtime: PipelineRuntime,
    *,
    security_interval: int = SECURITY_INTERVAL_SECONDS,
    release_issue_interval: int = RELEASE_ISSUE_INTERVAL_SECONDS,
) -> None:
    """Register the real tiered pipeline jobs onto ``scheduler`` (U6).

    One security tick + one release/issue tick PER watchlist repo (every repo is
    covered) + one daily trend tick. All real jobs are isolated (R19) and set
    ``max_instances=1`` + ``coalesce=True`` (KTD-F). Before registering the
    high-risk security / release-issue jobs, a single warning is logged when the
    outbound config has no email leg (KTD-D — black-hole email leg made visible).
    """
    settings = runtime.settings
    repos = settings.watchlist.repo_names()

    # KTD-D: high-risk alerts go out via RSS only when no email leg is configured.
    # Surface it ONCE at registration so the operator knows (RSS-only is legal).
    if not runtime.has_email_leg:
        logger.warning(
            "outbound email leg is NOT configured (no email_recipients/smtp_host); "
            "high-risk alerts will be delivered via RSS ONLY (KTD-D)"
        )

    # --- security: minute-level supply-chain tick, one job per repo ---
    for repo in repos:
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

    # --- release/issue: ~10-min event tick, one job per repo (full coverage) ---
    for repo in repos:
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


class Service:
    """Resident service with graceful start/stop.

    ``settings``/``creds`` are passed through to :func:`build_scheduler` (KTD-C):
    with neither, the Phase 0 placeholder runs; with ``settings`` the real tiered
    pipeline jobs are registered.
    """

    def __init__(
        self,
        scheduler: BackgroundScheduler | None = None,
        *,
        settings: Settings | None = None,
        creds: Credentials | None = None,
    ) -> None:
        self.scheduler = build_scheduler(scheduler, settings=settings, creds=creds)

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
