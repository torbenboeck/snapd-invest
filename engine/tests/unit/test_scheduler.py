"""Tests for `snapd_invest.scheduler`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker

from snapd_invest.broker import PaperBroker
from snapd_invest.config import Settings
from snapd_invest.llm import FakeLlmProvider
from snapd_invest.risk import RiskConfig
from snapd_invest.scheduler import build_default_jobs

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from snapd_invest.clock import FakeClock


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
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker=PaperBroker(fake_clock),
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
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker=PaperBroker(fake_clock),
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
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker=PaperBroker(fake_clock),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        microtrader_job = next(j for j in jobs if j.job_id == "microtrader_tick")

        # Direct invocation — no scheduler involved. Must NOT raise.
        await microtrader_job.handler()
