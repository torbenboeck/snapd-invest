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

from snapd_invest.broker import BrokerAuthError
from snapd_invest.broker.saxo import SaxoBroker
from snapd_invest.data import ensure_instrument, ensure_saxo_instrument, upsert_bars
from snapd_invest.pipeline import (
    expire_overdue_recommendations,
    instrument_type_for_exchange,
    parse_watchlist_entry,
    run_agent_once,
    run_microtrader_once,
)
from snapd_invest.portfolio import get_account_by_name
from snapd_invest.promotion import trivial_promotion_gate

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


def build_default_jobs(  # noqa: PLR0915 — handler closures are inherent to the wiring
    *,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    broker_factory: BrokerFactory,
    llm: ILlmProvider,
    risk_config: RiskConfig,
    settings: Settings,
    promotion_gate: PromotionGate = trivial_promotion_gate,
) -> list[JobConfig]:
    """Wire pipeline functions as APScheduler-friendly closures.

    Each handler opens its own session per tick, iterates the configured
    watchlist, and catches every exception so APScheduler keeps the job
    armed for the next interval.
    """

    async def _resolve_account_and_instrument(
        session: AsyncSession, entry: str
    ) -> tuple[Account, Instrument] | None:
        """Resolve a watchlist entry against the configured account.

        For SIM accounts the instrument has to be enriched with Saxo's UIC
        before any trading endpoint will accept it, so we route through
        `ensure_saxo_instrument` (which calls `/ref/v1/instruments` under
        the hood). For paper accounts the local row is sufficient.

        Returns None — with a structured warning — when the account is
        missing, when a SIM account hasn't had its OAuth identity
        backfilled yet, or when the Saxo instrument lookup fails.
        """
        symbol, exchange = parse_watchlist_entry(entry)
        account = await get_account_by_name(session, settings.default_account_name)
        if account is None:
            log.warning(
                "scheduler_skipped",
                reason="account_missing",
                account=settings.default_account_name,
            )
            return None

        instrument_type = instrument_type_for_exchange(exchange)

        if account.account_type == "sim":
            if account.saxo_account_key is None or account.saxo_client_key is None:
                log.warning(
                    "scheduler_skipped",
                    reason="saxo_identity_not_backfilled",
                    account=account.name,
                    entry=entry,
                )
                return None
            broker = broker_factory(account)
            if not isinstance(broker, SaxoBroker):
                log.warning(
                    "scheduler_skipped",
                    reason="sim_account_without_saxo_broker",
                    account=account.name,
                    entry=entry,
                )
                return None
            try:
                instrument = await ensure_saxo_instrument(
                    session,
                    broker,
                    symbol=symbol,
                    exchange=exchange,
                    instrument_type=instrument_type,
                )
            except (BrokerAuthError, ValueError):
                log.exception(
                    "scheduler_skipped",
                    reason="saxo_instrument_lookup_failed",
                    account=account.name,
                    entry=entry,
                )
                return None
        else:
            instrument = await ensure_instrument(
                session,
                symbol=symbol,
                exchange=exchange,
                instrument_type=instrument_type,
                currency=account.base_currency,
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
            except BrokerAuthError as exc:
                log.warning(
                    "scheduler_skipped",
                    job="microtrader_tick",
                    reason="saxo_reauth_required",
                    entry=entry,
                    detail=str(exc),
                )
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
            except BrokerAuthError as exc:
                log.warning(
                    "scheduler_skipped",
                    job="agent_tick",
                    reason="saxo_reauth_required",
                    entry=entry,
                    detail=str(exc),
                )
            except Exception:
                log.exception("scheduler_job_failed", job="agent_tick", entry=entry)

    async def _bar_refresh_handler() -> None:
        """Refresh recent OHLC bars for SIM watchlist instruments.

        Pulls candles from Saxo's `/chart/v1/charts` and upserts them via
        `upsert_bars`. Paper accounts are skipped — they'll be served by
        the yfinance provider (T-002) once it lands.
        """
        correlation_id = str(uuid.uuid4())
        for entry in settings.watchlist:
            try:
                async with session_factory() as session:
                    resolved = await _resolve_account_and_instrument(session, entry)
                    if resolved is None:
                        continue
                    account, instrument = resolved
                    if account.account_type != "sim":
                        log.debug(
                            "bar_refresh_skipped",
                            reason="non_sim_account",
                            account=account.name,
                            entry=entry,
                        )
                        continue
                    broker = broker_factory(account)
                    if not isinstance(broker, SaxoBroker):
                        continue
                    bars = await broker.get_charts(
                        session,
                        instrument=instrument,
                        interval=settings.bar_refresh_horizon,
                        count=settings.bar_refresh_count,
                    )
                    inserted = await upsert_bars(
                        session, instrument=instrument, bars=bars, source="saxo"
                    )
                    await session.commit()
                    log.info(
                        "bar_refresh_completed",
                        entry=entry,
                        correlation_id=correlation_id,
                        fetched=len(bars),
                        inserted=inserted,
                    )
            except BrokerAuthError as exc:
                log.warning(
                    "scheduler_skipped",
                    job="bar_refresh_tick",
                    reason="saxo_reauth_required",
                    entry=entry,
                    detail=str(exc),
                )
            except Exception:
                log.exception("scheduler_job_failed", job="bar_refresh_tick", entry=entry)

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
            job_id="bar_refresh_tick",
            minutes=settings.bar_refresh_interval_minutes,
            handler=_bar_refresh_handler,
        ),
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
