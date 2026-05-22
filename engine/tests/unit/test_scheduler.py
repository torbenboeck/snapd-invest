"""Tests for `snapd_invest.scheduler`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
from apscheduler.triggers.interval import IntervalTrigger
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from snapd_invest.broker import BrokerAuthError, IBroker, PaperBroker
from snapd_invest.broker.saxo import SaxoBroker, SaxoInstrumentHit
from snapd_invest.config import Settings
from snapd_invest.crypto import FernetCipher
from snapd_invest.data import BarData
from snapd_invest.llm import FakeLlmProvider
from snapd_invest.models import Account, Bar, Instrument
from snapd_invest.portfolio import create_account
from snapd_invest.promotion import Allowed
from snapd_invest.risk import RiskConfig
from snapd_invest.scheduler import JobConfig, build_default_jobs, build_scheduler

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from snapd_invest.broker import BrokerFactory
    from snapd_invest.clock import FakeClock


def _factory_for(broker: PaperBroker) -> BrokerFactory:
    def factory(_account: Account) -> IBroker:
        return broker

    return factory


class TestBuildDefaultJobs:
    def test_returns_four_jobs_with_settings_intervals(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            microtrader_interval_minutes=2,
            agent_interval_minutes=10,
            recommendation_expire_interval_minutes=7,
            bar_refresh_interval_minutes=3,
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
        assert set(by_id) == {
            "bar_refresh_tick",
            "microtrader_tick",
            "agent_tick",
            "expire_overdue",
        }
        assert by_id["bar_refresh_tick"].minutes == 3
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


class _StubSaxoBroker(SaxoBroker):
    """`SaxoBroker` subclass that stubs the two methods the scheduler calls.

    Real `SaxoBroker` would hit the network; we override `search_instruments`
    and `get_charts` so the scheduler tick is purely in-memory. The HTTP
    client is constructed but never used — it would only be touched if the
    base class's `_authed_request` ran, which our overrides bypass.
    """

    def __init__(self, fake_clock: FakeClock, bars: list[BarData] | None = None) -> None:
        super().__init__(
            client=httpx.AsyncClient(),
            clock=fake_clock,
            cipher=FernetCipher(Fernet.generate_key()),
            client_id="x",
            account_id="any",
        )
        self._stub_bars = bars or []
        self.charts_calls: list[tuple[str, str, int]] = []
        self.search_calls: list[tuple[str, str]] = []

    async def search_instruments(  # type: ignore[override]
        self, _session: AsyncSession, keywords: str, *, asset_type: str
    ) -> list[SaxoInstrumentHit]:
        self.search_calls.append((keywords, asset_type))
        return [
            SaxoInstrumentHit(
                uic=16,
                symbol=keywords,
                asset_type=asset_type,
                description=keywords,
            )
        ]

    async def get_charts(  # type: ignore[override]
        self,
        _session: AsyncSession,
        *,
        instrument: Instrument,
        interval: str = "1d",
        count: int = 250,
    ) -> list[BarData]:
        self.charts_calls.append((instrument.symbol, interval, count))
        return self._stub_bars


def _factory_returning(broker: IBroker) -> BrokerFactory:
    def factory(_account: Account) -> IBroker:
        return broker

    return factory


class TestBarRefreshHandler:
    async def test_skips_non_sim_account(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        """Paper accounts have no Saxo identity backfilled — bar refresh
        must walk past them quietly."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            await create_account(session, fake_clock, name="paper")
            await session.commit()

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            watchlist=["AAPL@NASDAQ"],
            default_account_name="paper",
        )
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_returning(PaperBroker(fake_clock)),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        refresh_job = next(j for j in jobs if j.job_id == "bar_refresh_tick")

        await refresh_job.handler()  # must not raise

        async with factory() as session:
            bars = list((await session.execute(select(Bar))).scalars().all())
        assert bars == []

    async def test_skips_sim_account_without_saxo_identity(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        """A SIM account that hasn't completed OAuth identity backfill
        is skipped — calling get_charts without `saxo_account_key`
        would raise BrokerAuthError downstream."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            await create_account(session, fake_clock, name="sim", account_type="sim")
            await session.commit()

        fake_broker = _StubSaxoBroker(fake_clock)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            watchlist=["EURDKK@FX"],
            default_account_name="sim",
        )
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_returning(fake_broker),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        refresh_job = next(j for j in jobs if j.job_id == "bar_refresh_tick")

        await refresh_job.handler()

        assert fake_broker.charts_calls == []

    async def test_happy_path_upserts_bars_for_sim_account(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        """A SIM account with backfilled identity should have bars upserted
        from the broker's `get_charts` response."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            account = await create_account(session, fake_clock, name="sim", account_type="sim")
            account.saxo_client_key = "client-1"
            account.saxo_account_key = "acc-1"
            await session.commit()

        bars = [
            BarData(
                instrument_symbol="EURDKK",
                interval="1d",
                timestamp=datetime(2026, 5, 20, tzinfo=UTC),
                open=Decimal("7.4560"),
                high=Decimal("7.4570"),
                low=Decimal("7.4555"),
                close=Decimal("7.4565"),
                volume=Decimal("0"),
            ),
            BarData(
                instrument_symbol="EURDKK",
                interval="1d",
                timestamp=datetime(2026, 5, 21, tzinfo=UTC),
                open=Decimal("7.4566"),
                high=Decimal("7.4580"),
                low=Decimal("7.4562"),
                close=Decimal("7.4575"),
                volume=Decimal("0"),
            ),
        ]
        fake_broker = _StubSaxoBroker(fake_clock, bars=bars)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            watchlist=["EURDKK@FX"],
            default_account_name="sim",
            bar_refresh_horizon="1d",
            bar_refresh_count=250,
        )
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_returning(fake_broker),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        refresh_job = next(j for j in jobs if j.job_id == "bar_refresh_tick")

        await refresh_job.handler()

        assert fake_broker.charts_calls == [("EURDKK", "1d", 250)]
        async with factory() as session:
            rows = list((await session.execute(select(Bar))).scalars().all())
        assert len(rows) == 2

    async def test_broker_exception_does_not_propagate(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        """A broker error during a refresh must not break the scheduler tick."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            account = await create_account(session, fake_clock, name="sim", account_type="sim")
            account.saxo_client_key = "client-1"
            account.saxo_account_key = "acc-1"
            await session.commit()

        class _BrokenBroker(_StubSaxoBroker):
            async def get_charts(  # type: ignore[override]
                self, *_args: object, **_kw: object
            ) -> list[BarData]:
                raise RuntimeError("simulated transport failure")

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            watchlist=["EURDKK@FX"],
            default_account_name="sim",
        )
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_returning(_BrokenBroker(fake_clock)),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        refresh_job = next(j for j in jobs if j.job_id == "bar_refresh_tick")

        await refresh_job.handler()  # must not raise

    async def test_broker_auth_error_is_logged_quietly(
        self, db_engine: AsyncEngine, fake_clock: FakeClock
    ) -> None:
        """Saxo session expiry is a routine "need re-auth" condition, not a
        crash. The handler should swallow `BrokerAuthError` without dumping
        a stack trace — operators re-authenticate via the CLI."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            account = await create_account(session, fake_clock, name="sim", account_type="sim")
            account.saxo_client_key = "client-1"
            account.saxo_account_key = "acc-1"
            await session.commit()

        class _ExpiredAuthBroker(_StubSaxoBroker):
            async def get_charts(  # type: ignore[override]
                self, *_args: object, **_kw: object
            ) -> list[BarData]:
                raise BrokerAuthError("saxo token refresh failed: 401")

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            watchlist=["EURDKK@FX"],
            default_account_name="sim",
        )
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker_factory=_factory_returning(_ExpiredAuthBroker(fake_clock)),
            promotion_gate=lambda _account, _broker: Allowed(),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        refresh_job = next(j for j in jobs if j.job_id == "bar_refresh_tick")

        await refresh_job.handler()  # must not raise


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
