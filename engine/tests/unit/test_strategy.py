"""Tests for `algo_invest.strategy`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from algo_invest.clock import FakeClock
from algo_invest.data import BarData, ensure_instrument, upsert_bars
from algo_invest.portfolio import create_account
from algo_invest.strategy import SMACrossoverConfig, SMACrossoverStrategy


def _make_bars(symbol: str, closes: list[Decimal]) -> list[BarData]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        BarData(
            instrument_symbol=symbol,
            interval="1d",
            timestamp=base + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=Decimal("1000"),
        )
        for i, c in enumerate(closes)
    ]


class TestSMACrossoverConfig:
    def test_rejects_inverted_periods(self) -> None:
        with pytest.raises(ValueError, match="short_period"):
            SMACrossoverStrategy(SMACrossoverConfig(short_period=50, long_period=20))


class TestSMACrossoverStrategy:
    async def test_no_signal_when_insufficient_history(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session, symbol="X", exchange="Y", instrument_type="stock", currency="USD"
        )
        await upsert_bars(
            db_session,
            instrument=instrument,
            bars=_make_bars("X", [Decimal("100")] * 10),
            source="t",
        )
        strategy = SMACrossoverStrategy(
            SMACrossoverConfig(short_period=5, long_period=20)
        )

        signals = await strategy.run(
            db_session,
            account=account,
            instrument=instrument,
            emitted_at=fake_clock.now(),
            correlation_id=None,
        )
        assert signals == []

    async def test_golden_cross_emits_buy(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session, symbol="X", exchange="Y", instrument_type="stock", currency="USD"
        )
        # Construct a series where the short SMA crosses up through the long SMA.
        # Long period 5, short period 2.
        # Closes: lots of low prices, then a sharp rise at the end.
        closes = (
            [Decimal("100")] * 10
            + [Decimal("90")] * 5    # short and long both decline
            + [Decimal("80"), Decimal("80")]  # short dips below long firmly
            + [Decimal("200"), Decimal("200")]  # short surges above long
        )
        await upsert_bars(
            db_session, instrument=instrument, bars=_make_bars("X", closes), source="t"
        )
        strategy = SMACrossoverStrategy(
            SMACrossoverConfig(short_period=2, long_period=5)
        )

        signals = await strategy.run(
            db_session,
            account=account,
            instrument=instrument,
            emitted_at=fake_clock.now(),
            correlation_id=None,
        )
        assert len(signals) == 1
        assert signals[0].action == "buy"
        assert signals[0].source == "sma_crossover"

    async def test_death_cross_emits_sell(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session, symbol="X", exchange="Y", instrument_type="stock", currency="USD"
        )
        closes = (
            [Decimal("100")] * 10
            + [Decimal("150")] * 5
            + [Decimal("200"), Decimal("200")]
            + [Decimal("50"), Decimal("50")]
        )
        await upsert_bars(
            db_session, instrument=instrument, bars=_make_bars("X", closes), source="t"
        )
        strategy = SMACrossoverStrategy(
            SMACrossoverConfig(short_period=2, long_period=5)
        )

        signals = await strategy.run(
            db_session,
            account=account,
            instrument=instrument,
            emitted_at=fake_clock.now(),
            correlation_id=None,
        )
        assert len(signals) == 1
        assert signals[0].action == "sell"
