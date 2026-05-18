"""Scheduled jobs.

Thin wrapper over APScheduler. Each registered job calls into pipeline.py
functions — the same code path used by the manual POST /v1/run-once and
POST /v1/agents/run routes.

The scheduler is started by the FastAPI lifespan in `api.py`. In tests,
construct `build_default_jobs(...)` directly and invoke handlers; the
integration test in test_scheduler.py uses a real `AsyncIOScheduler` with
a 1-second interval.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from snapd_invest.data import ensure_instrument
from snapd_invest.pipeline import (
    expire_overdue_recommendations,
    parse_watchlist_entry,
    run_agent_once,
    run_microtrader_once,
)
from snapd_invest.portfolio import get_account_by_name

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from snapd_invest.broker import BrokerFactory
    from snapd_invest.clock import Clock
    from snapd_invest.config import Settings
    from snapd_invest.llm import ILlmProvider
    from snapd_invest.models import Account, Instrument
    from snapd_invest.promotion import PromotionGate
    from snapd_invest.risk import RiskConfig

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

JobFn = Callable[[], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class JobConfig:
    job_id: str
    minutes: int
    handler: JobFn


def build_scheduler(jobs: list[JobConfig]) -> AsyncIOScheduler:
    """Create an `AsyncIOScheduler` with the provided jobs.

    Registers an `EVENT_JOB_ERROR` listener as a backstop — the per-handler
    try/except inside `build_default_jobs` is the primary defense.
    """
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
    scheduler.add_listener(_log_job_error, EVENT_JOB_ERROR)
    return scheduler


def _log_job_error(event: JobExecutionEvent) -> None:
    """APScheduler-level backstop. Logs any exception that slips past the
    per-handler try/except in `build_default_jobs`."""
    log.error(
        "scheduler_job_error_event",
        job_id=event.job_id,
        scheduled_run_time=event.scheduled_run_time.isoformat(),
        exception=str(event.exception) if event.exception else None,
    )


def build_default_jobs(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    broker_factory: BrokerFactory,
    promotion_gate: PromotionGate,
    llm: ILlmProvider,
    risk_config: RiskConfig,
    settings: Settings,
) -> list[JobConfig]:
    """Wire pipeline functions as APScheduler-friendly closures.

    Each handler opens its own session per tick, iterates the configured
    watchlist, and catches every exception so APScheduler keeps the job
    armed for the next interval.
    """

    async def _resolve_account_and_instrument(
        session: AsyncSession, entry: str
    ) -> tuple[Account, Instrument] | None:
        symbol, exchange = parse_watchlist_entry(entry)
        account = await get_account_by_name(session, settings.default_account_name)
        if account is None:
            log.warning(
                "scheduler_skipped",
                reason="account_missing",
                account=settings.default_account_name,
            )
            return None
        instrument = await ensure_instrument(
            session,
            symbol=symbol,
            exchange=exchange,
            instrument_type="stock",
            currency="USD",
        )
        return account, instrument

    async def _microtrader_handler() -> None:
        correlation_id = str(uuid.uuid4())
        for entry in settings.watchlist:
            try:
                async with session_factory() as session:
                    resolved = await _resolve_account_and_instrument(session, entry)
                    if resolved is None:
                        continue
                    account, instrument = resolved
                    await run_microtrader_once(
                        session,
                        clock,
                        broker_factory,
                        promotion_gate,
                        risk_config,
                        account=account,
                        instrument=instrument,
                        correlation_id=correlation_id,
                    )
                    await session.commit()
            except Exception:
                log.exception("scheduler_job_failed", job="microtrader_tick", entry=entry)

    async def _agent_handler() -> None:
        correlation_id = str(uuid.uuid4())
        for entry in settings.watchlist:
            try:
                async with session_factory() as session:
                    resolved = await _resolve_account_and_instrument(session, entry)
                    if resolved is None:
                        continue
                    account, instrument = resolved
                    await run_agent_once(
                        session,
                        clock,
                        llm,
                        account=account,
                        instrument=instrument,
                        correlation_id=correlation_id,
                    )
                    await session.commit()
            except Exception:
                log.exception("scheduler_job_failed", job="agent_tick", entry=entry)

    async def _expire_handler() -> None:
        try:
            async with session_factory() as session:
                expired = await expire_overdue_recommendations(session, clock)
                await session.commit()
                if expired:
                    log.warning("recommendations_expired", count=expired)
                else:
                    log.info("recommendations_expired", count=0)
        except Exception:
            log.exception("scheduler_job_failed", job="expire_overdue")

    return [
        JobConfig(
            job_id="microtrader_tick",
            minutes=settings.microtrader_interval_minutes,
            handler=_microtrader_handler,
        ),
        JobConfig(
            job_id="agent_tick",
            minutes=settings.agent_interval_minutes,
            handler=_agent_handler,
        ),
        JobConfig(
            job_id="expire_overdue",
            minutes=settings.recommendation_expire_interval_minutes,
            handler=_expire_handler,
        ),
    ]
