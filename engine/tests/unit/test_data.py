"""Tests for `snapd_invest.data`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from snapd_invest.clock import FakeClock
from snapd_invest.data import (
    BarData,
    FakeMarketDataProvider,
    ensure_instrument,
    load_recent_bars,
    refresh_bars,
    upsert_bars,
)


def _bar(symbol: str, ts: datetime, close: Decimal) -> BarData:
    return BarData(
        instrument_symbol=symbol,
        interval="1d",
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000"),
    )


class TestEnsureInstrument:
    async def test_creates_when_missing(self, db_session: AsyncSession) -> None:
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        assert instrument.id
        assert instrument.symbol == "AAPL"

    async def test_returns_existing(self, db_session: AsyncSession) -> None:
        first = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        second = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        assert first.id == second.id


class TestUpsertBars:
    async def test_inserts_new(self, db_session: AsyncSession) -> None:
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        base = datetime(2026, 5, 1, tzinfo=UTC)
        bars = [_bar("AAPL", base + timedelta(days=i), Decimal("100")) for i in range(3)]

        inserted = await upsert_bars(
            db_session, instrument=instrument, bars=bars, source="test"
        )
        assert inserted == 3

    async def test_skips_duplicates(self, db_session: AsyncSession) -> None:
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        base = datetime(2026, 5, 1, tzinfo=UTC)
        bars = [_bar("AAPL", base, Decimal("100"))]

        await upsert_bars(db_session, instrument=instrument, bars=bars, source="test")
        inserted_again = await upsert_bars(
            db_session, instrument=instrument, bars=bars, source="test"
        )
        assert inserted_again == 0


class TestLoadRecentBars:
    async def test_returns_oldest_first(self, db_session: AsyncSession) -> None:
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        base = datetime(2026, 5, 1, tzinfo=UTC)
        bars = [_bar("AAPL", base + timedelta(days=i), Decimal(100 + i)) for i in range(5)]
        await upsert_bars(db_session, instrument=instrument, bars=bars, source="test")

        loaded = await load_recent_bars(
            db_session, instrument=instrument, interval="1d", limit=3
        )
        # Most recent 3, oldest first
        assert [b.close for b in loaded] == [Decimal("102"), Decimal("103"), Decimal("104")]


class TestRefreshBars:
    async def test_calls_provider_and_persists(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        provider = FakeMarketDataProvider()
        base = datetime(2026, 5, 1, tzinfo=UTC)
        provider.seed(
            symbol="BTC-USD",
            exchange="BINANCE",
            interval="1h",
            bars=[_bar("BTC-USD", base + timedelta(hours=i), Decimal(50000 + i)) for i in range(4)],
        )
        instrument = await ensure_instrument(
            db_session,
            symbol="BTC-USD",
            exchange="BINANCE",
            instrument_type="crypto",
            currency="USD",
        )

        inserted = await refresh_bars(
            db_session,
            fake_clock,
            provider,
            instrument=instrument,
            interval="1h",
            limit=10,
            source="fake",
        )
        assert inserted == 4
