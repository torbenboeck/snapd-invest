"""Tests for `snapd_invest.scheduler`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from snapd_invest.broker import IBroker, PaperBroker
from snapd_invest.config import Settings
from snapd_invest.llm import FakeLlmProvider
from snapd_invest.promotion import Allowed
from snapd_invest.risk import RiskConfig
from snapd_invest.scheduler import JobConfig, build_default_jobs, build_scheduler

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from snapd_invest.broker import BrokerFactory
    from snapd_invest.clock import FakeClock
    from snapd_invest.models import Account


def _factory_for(broker: PaperBroker) -> BrokerFactory:
    def factory(_account: Account) -> IBroker:
        return broker

    return factory


class TestBuildDefaultJobs:
    def test_returns_three_jobs_with_settings_intervals(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            microtrader_interval_minutes=2,
            agent_interval_minutes=10,
            recommendation_expire_interval_minutes=7,
        )
        broker = PaperBroker(fake_clock)
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_for(broker),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )

        by_id = {job.job_id: job for job in jobs}
        assert set(by_id) == {"microtrader_tick", "agent_tick", "expire_overdue"}
        assert by_id["microtrader_tick"].minutes == 2
        assert by_id["agent_tick"].minutes == 10
        assert by_id["expire_overdue"].minutes == 7

    def test_job_ids_are_unique(self, db_engine: AsyncEngine, fake_clock: FakeClock) -> None:
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        broker = PaperBroker(fake_clock)
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_for(broker),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=Settings(_env_file=None),  # type: ignore[call-arg]
        )
        ids = [j.job_id for j in jobs]
        assert len(ids) == len(set(ids))


class TestHandlerErrorIsolation:
    async def test_handler_swallows_invalid_watchlist_entry(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        """A broken watchlist entry must not propagate out of the handler —
        APScheduler needs to keep the job armed for the next interval."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            watchlist=["INVALID-NO-AT-SIGN"],
        )
        broker = PaperBroker(fake_clock)
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_for(broker),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        microtrader_job = next(j for j in jobs if j.job_id == "microtrader_tick")

        # Direct invocation — no scheduler involved. Must NOT raise.
        await microtrader_job.handler()


class TestSchedulerIntegration:
    async def test_scheduler_fires_handler(self) -> None:
        """Boot a real AsyncIOScheduler with a 1-second interval and verify
        the handler runs at least once before we shut the scheduler down."""
        counter = {"calls": 0}
        done = asyncio.Event()

        async def handler() -> None:
            counter["calls"] += 1
            done.set()

        scheduler = build_scheduler([JobConfig(job_id="probe", minutes=1, handler=handler)])
        scheduler.start()
        # Replace the 1-minute trigger with a sub-second one for test speed.
        scheduler.reschedule_job("probe", trigger=IntervalTrigger(seconds=1))
        try:
            await asyncio.wait_for(done.wait(), timeout=3.0)
        finally:
            scheduler.shutdown(wait=False)

        assert counter["calls"] >= 1

    async def test_scheduler_survives_handler_exception(self) -> None:
        """A handler that raises must not crash the scheduler — subsequent
        ticks still fire."""
        calls = 0
        done_after_failure = asyncio.Event()

        async def flaky_handler() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            done_after_failure.set()

        scheduler = build_scheduler([JobConfig(job_id="flaky", minutes=1, handler=flaky_handler)])
        scheduler.start()
        scheduler.reschedule_job("flaky", trigger=IntervalTrigger(seconds=1))
        try:
            await asyncio.wait_for(done_after_failure.wait(), timeout=4.0)
        finally:
            scheduler.shutdown(wait=False)

        assert calls >= 2
