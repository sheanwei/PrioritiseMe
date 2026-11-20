import asyncio

import pytest

from taskbot.core import scheduler


def test_run_all_once_runs_sync_and_async_jobs_and_isolates_failures():
    ran = []

    def sync_job():
        ran.append("sync")

    async def async_job():
        ran.append("async")

    def broken():
        raise RuntimeError("boom")

    scheduler.register_job("t.sync", 10, sync_job)
    scheduler.register_job("t.async", 10, async_job)
    scheduler.register_job("t.broken", 10, broken)
    results = scheduler.run_all_once()
    assert results == {"t.sync": True, "t.async": True, "t.broken": False}
    assert sorted(ran) == ["async", "sync"]


def test_register_replaces_and_validates():
    scheduler.register_job("t.job", 5, lambda: None)
    scheduler.register_job("t.job", 7, lambda: None)
    assert [(j.name, j.every_seconds) for j in scheduler.jobs()] == [("t.job", 7)]
    with pytest.raises(ValueError):
        scheduler.register_job("t.bad", 0, lambda: None)


def test_scheduler_loop_ticks_until_stopped():
    ticks = []
    scheduler.register_job("t.tick", 0.01, lambda: ticks.append(1))

    async def run():
        sched = scheduler.Scheduler()
        sched.start()
        await asyncio.sleep(0.1)
        await sched.stop()

    asyncio.run(run())
    assert len(ticks) >= 2
