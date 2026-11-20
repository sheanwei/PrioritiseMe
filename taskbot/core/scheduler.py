"""Tiny in-process job scheduler (one asyncio task per job).

Slices register jobs from their ``startup()`` hook::

    from taskbot.core.scheduler import register_job
    register_job("s2_timebox.expire", 30, expire_timeboxes)

Jobs may be sync (run in a worker thread) or async. They must be idempotent:
they run on startup and then every ``every_seconds``. A failing job is logged
and retried on its next tick; it never stops other jobs.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger("taskbot.scheduler")

JobFn = Callable[[], None] | Callable[[], Awaitable[None]]


@dataclass
class Job:
    name: str
    every_seconds: float
    fn: JobFn
    run_on_start: bool = True


_jobs: dict[str, Job] = {}


def register_job(
    name: str, every_seconds: float, fn: JobFn, *, run_on_start: bool = True
) -> None:
    """Register (or replace) a job. Prefix ``name`` with your slice, e.g. ``s3_resurface.daily``."""
    if every_seconds <= 0:
        raise ValueError("every_seconds must be positive")
    _jobs[name] = Job(name, every_seconds, fn, run_on_start)


def jobs() -> list[Job]:
    return list(_jobs.values())


def reset() -> None:
    """Forget all jobs (tests)."""
    _jobs.clear()


async def run_job(job: Job) -> bool:
    """Run a job once. Returns False (and logs) if it raised."""
    try:
        if inspect.iscoroutinefunction(job.fn):
            await job.fn()
        else:
            await asyncio.to_thread(job.fn)
        return True
    except Exception:
        log.exception("job %s failed", job.name)
        return False


def run_all_once() -> dict[str, bool]:
    """Run every registered job once, synchronously (tests / manual triggers)."""

    async def _run() -> dict[str, bool]:
        return {job.name: await run_job(job) for job in jobs()}

    return asyncio.run(_run())


class Scheduler:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        for job in jobs():
            self._tasks.append(asyncio.create_task(self._loop(job), name=f"job:{job.name}"))

    async def _loop(self, job: Job) -> None:
        if not job.run_on_start:
            await asyncio.sleep(job.every_seconds)
        while True:
            await run_job(job)
            await asyncio.sleep(job.every_seconds)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
