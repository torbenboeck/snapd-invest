"""Scheduled jobs.

Thin wrapper over APScheduler. Each registered job calls the same handler
function a user would trigger through `POST /v1/run-once`. One code path.

The scheduler is started by the FastAPI lifespan in `api.py`. In tests, we
construct handlers directly and bypass APScheduler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

JobFn = Callable[[], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class JobConfig:
    job_id: str
    minutes: int
    handler: JobFn


def build_scheduler(jobs: list[JobConfig]) -> AsyncIOScheduler:
    """Create an `AsyncIOScheduler` with the provided jobs."""
    scheduler = AsyncIOScheduler()
    for job in jobs:
        scheduler.add_job(
            job.handler,
            trigger=IntervalTrigger(minutes=job.minutes),
            id=job.job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    return scheduler
