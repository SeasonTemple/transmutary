"""U5 service tests — scheduler boot, placeholder registration, fault isolation."""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from transmutary import service
from transmutary.service import Service, _isolated, build_scheduler


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
