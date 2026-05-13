"""Tests for `snapd_invest.agent`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from snapd_invest.agent import (
    CONSERVATIVE_VALUE,
    Personality,
    ensure_default_agent,
    run_agent,
)
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.llm import FakeLlmProvider, LlmResponse
from snapd_invest.portfolio import create_account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock


async def _setup_world(session: AsyncSession, clock: FakeClock) -> tuple[object, object, object]:
    account = await create_account(session, clock, name="paper", initial_cash=Decimal("100000"))
    instrument = await ensure_instrument(
        session,
        symbol="AAPL",
        exchange="NASDAQ",
        instrument_type="stock",
        currency="USD",
    )
    await upsert_bars(
        session,
        instrument=instrument,
        bars=[
            BarData(
                instrument_symbol="AAPL",
                interval="1d",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                open=Decimal("150"),
                high=Decimal("150"),
                low=Decimal("150"),
                close=Decimal("150"),
                volume=Decimal("1000"),
            )
        ],
        source="test",
    )
    agent = await ensure_default_agent(session, clock, account=account)
    return account, instrument, agent


class TestEnsureDefaultAgent:
    async def test_creates_when_missing(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        agent = await ensure_default_agent(db_session, fake_clock, account=account)
        assert agent.id
        assert agent.name == CONSERVATIVE_VALUE.name

    async def test_returns_existing(self, db_session: AsyncSession, fake_clock: FakeClock) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        first = await ensure_default_agent(db_session, fake_clock, account=account)
        second = await ensure_default_agent(db_session, fake_clock, account=account)
        assert first.id == second.id


class TestRunAgent:
    async def test_produces_signal_above_conviction_threshold(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        _, instrument, agent = await _setup_world(db_session, fake_clock)
        llm = FakeLlmProvider()
        llm.enqueue_json(
            {
                "summary": "AAPL looks cheap",
                "signals": [
                    {
                        "instrument_symbol": "AAPL",
                        "instrument_exchange": "NASDAQ",
                        "action": "buy",
                        "quantity": 5,
                        "conviction": 0.8,
                        "rationale": "below intrinsic",
                    }
                ],
            }
        )

        result = await run_agent(
            db_session,
            fake_clock,
            llm,
            agent=agent,
            personality=CONSERVATIVE_VALUE,
            watchlist=[instrument],
        )
        assert len(result.signals) == 1
        signal = result.signals[0]
        assert signal.action == "buy"
        assert signal.quantity == Decimal("5")
        assert signal.source == f"agent:{agent.name}"

    async def test_filters_low_conviction(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        _, instrument, agent = await _setup_world(db_session, fake_clock)
        llm = FakeLlmProvider()
        llm.enqueue_json(
            {
                "summary": "weak signal",
                "signals": [
                    {
                        "instrument_symbol": "AAPL",
                        "instrument_exchange": "NASDAQ",
                        "action": "buy",
                        "quantity": 5,
                        "conviction": 0.4,  # below 0.7 threshold
                        "rationale": "weak",
                    }
                ],
            }
        )
        result = await run_agent(
            db_session,
            fake_clock,
            llm,
            agent=agent,
            personality=CONSERVATIVE_VALUE,
            watchlist=[instrument],
        )
        assert result.signals == []

    async def test_filters_off_watchlist(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        _, instrument, agent = await _setup_world(db_session, fake_clock)
        llm = FakeLlmProvider()
        llm.enqueue_json(
            {
                "summary": "off watchlist",
                "signals": [
                    {
                        "instrument_symbol": "TSLA",
                        "instrument_exchange": "NASDAQ",
                        "action": "buy",
                        "quantity": 1,
                        "conviction": 0.9,
                        "rationale": "...",
                    }
                ],
            }
        )
        result = await run_agent(
            db_session,
            fake_clock,
            llm,
            agent=agent,
            personality=CONSERVATIVE_VALUE,
            watchlist=[instrument],
        )
        assert result.signals == []

    async def test_raises_when_llm_returns_no_parsed(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        _, instrument, agent = await _setup_world(db_session, fake_clock)
        llm = FakeLlmProvider()
        llm.enqueue(LlmResponse(text="not-json", parsed=None))

        with pytest.raises(ValueError, match="parseable JSON"):
            await run_agent(
                db_session,
                fake_clock,
                llm,
                agent=agent,
                personality=CONSERVATIVE_VALUE,
                watchlist=[instrument],
            )

    async def test_custom_personality(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account, instrument, _ = await _setup_world(db_session, fake_clock)
        custom = Personality(
            name="aggressive",
            description="High conviction trader",
            risk_tolerance=Decimal("0.9"),
            horizon_days=7,
            min_conviction=Decimal("0.4"),
            interests=["crypto"],
        )
        agent = await ensure_default_agent(
            db_session, fake_clock, account=account, personality=custom
        )
        llm = FakeLlmProvider()
        llm.enqueue_json(
            {
                "summary": "ok",
                "signals": [
                    {
                        "instrument_symbol": "AAPL",
                        "instrument_exchange": "NASDAQ",
                        "action": "buy",
                        "quantity": 1,
                        "conviction": 0.5,
                        "rationale": "ok",
                    }
                ],
            }
        )
        result = await run_agent(
            db_session,
            fake_clock,
            llm,
            agent=agent,
            personality=custom,
            watchlist=[instrument],
        )
        assert len(result.signals) == 1  # 0.5 >= 0.4 (custom threshold)
