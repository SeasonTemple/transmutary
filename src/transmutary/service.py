"""Service entry point + scheduler bootstrap (U5, R19).

Phase 0 registers a no-op placeholder job to confirm the scheduler boots; real
tiered jobs (security minutes / releases ~10min / issues per-repo priority /
trend daily) are wired in Phase 1 as collectors land. A single job raising must
NOT bring the scheduler down — failures are isolated and logged, and the next
run proceeds normally.
"""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("transmutary.service")

# Phase 0 placeholder cadence (seconds). Real cadences come from config in Phase 1.
PLACEHOLDER_INTERVAL_SECONDS = 60


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
    interval_seconds: int = PLACEHOLDER_INTERVAL_SECONDS,
) -> BackgroundScheduler:
    """Build a scheduler and register Phase 0 placeholder job(s).

    A scheduler may be injected (tests pass a fake / paused one). Real tiered
    collector jobs are registered in Phase 1.
    """
    scheduler = scheduler if scheduler is not None else BackgroundScheduler()
    register_jobs(scheduler, interval_seconds=interval_seconds)
    return scheduler


def register_jobs(
    scheduler: BackgroundScheduler, *, interval_seconds: int = PLACEHOLDER_INTERVAL_SECONDS
) -> None:
    """Register Phase 0 jobs onto an existing scheduler with isolation wrapping."""
    scheduler.add_job(
        _isolated("placeholder", _placeholder_job),
        trigger="interval",
        seconds=interval_seconds,
        id="placeholder",
        replace_existing=True,
    )


class Service:
    """Resident service with graceful start/stop."""

    def __init__(self, scheduler: BackgroundScheduler | None = None) -> None:
        self.scheduler = build_scheduler(scheduler)

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
        job_ids = [j.id for j in self.scheduler.get_jobs()]
        logger.info("transmutary service started; jobs: %s", job_ids)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        logger.info("transmutary service stopped")


def main() -> None:  # pragma: no cover - real entrypoint wired in Phase 1
    logging.basicConfig(level=logging.INFO)
    service = Service()
    service.start()
    try:
        import time

        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        service.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
