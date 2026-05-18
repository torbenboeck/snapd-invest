"""Tests for `snapd_invest.data`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from snapd_invest.broker.saxo import SaxoInstrumentHit
from snapd_invest.data import (
    BarData,
    FakeMarketDataProvider,
    ensure_instrument,
    ensure_saxo_instrument,
    load_recent_bars,
    refresh_bars,
    upsert_bars,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock


# ---------------------------------------------------------------------------
# Fake broker for ensure_saxo_instrument tests
# ---------------------------------------------------------------------------


class _FakeSaxoBroker:
    """Minimal stub that satisfies the search_instruments interface."""

    def __init__(self, hits: list[SaxoInstrumentHit]) -> None:
        self._hits = hits
        self.call_count = 0

    async def search_instruments(
        self, _session: object, _keywords: str, *, asset_type: str
    ) -> list[SaxoInstrumentHit]:
        _ = asset_type
        self.call_count += 1
        return self._hits


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

        inserted = await upsert_bars(db_session, instrument=instrument, bars=bars, source="test")
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

        loaded = await load_recent_bars(db_session, instrument=instrument, interval="1d", limit=3)
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


class TestEnsureSaxoInstrument:
    async def test_returns_existing_instrument_with_uic_without_calling_broker(
        self, db_session: AsyncSession
    ) -> None:
        """If saxo_uic is already populated, broker must NOT be called."""
        instrument = await ensure_instrument(
            db_session,
            symbol="EURDKK",
            exchange="FX",
            instrument_type="fx",
            currency="DKK",
        )
        instrument.saxo_uic = 16
        instrument.saxo_asset_type = "FxSpot"
        await db_session.flush()

        broker = _FakeSaxoBroker(hits=[])
        result = await ensure_saxo_instrument(
            db_session,
            broker,
            symbol="EURDKK",
            exchange="FX",  # type: ignore[arg-type]
        )

        assert result.id == instrument.id
        assert result.saxo_uic == 16
        assert broker.call_count == 0

    async def test_instrument_exists_without_uic_calls_broker_and_persists(
        self, db_session: AsyncSession
    ) -> None:
        """If instrument exists but saxo_uic is None, broker is called and result is persisted."""
        instrument = await ensure_instrument(
            db_session,
            symbol="EURDKK",
            exchange="FX",
            instrument_type="fx",
            currency="DKK",
        )
        assert instrument.saxo_uic is None

        hit = SaxoInstrumentHit(uic=16, symbol="EURDKK", asset_type="FxSpot", description="EUR/DKK")
        broker = _FakeSaxoBroker(hits=[hit])
        result = await ensure_saxo_instrument(
            db_session,
            broker,
            symbol="EURDKK",
            exchange="FX",  # type: ignore[arg-type]
        )

        assert result.id == instrument.id
        assert result.saxo_uic == 16
        assert result.saxo_asset_type == "FxSpot"
        assert broker.call_count == 1

    async def test_instrument_not_found_creates_row_calls_broker_and_persists(
        self, db_session: AsyncSession
    ) -> None:
        """If instrument doesn't exist, it is created, broker called, and UIC stored."""
        hit = SaxoInstrumentHit(uic=42, symbol="USDDKK", asset_type="FxSpot", description="USD/DKK")
        broker = _FakeSaxoBroker(hits=[hit])
        result = await ensure_saxo_instrument(
            db_session,
            broker,
            symbol="USDDKK",
            exchange="FX",  # type: ignore[arg-type]
        )

        assert result.symbol == "USDDKK"
        assert result.saxo_uic == 42
        assert result.saxo_asset_type == "FxSpot"
        assert broker.call_count == 1

    async def test_broker_returns_no_match_raises_value_error(
        self, db_session: AsyncSession
    ) -> None:
        """If broker returns no matching symbol, ValueError is raised."""
        broker = _FakeSaxoBroker(hits=[])
        with pytest.raises(ValueError, match="symbol='EURDKK'"):
            await ensure_saxo_instrument(
                db_session,
                broker,
                symbol="EURDKK",
                exchange="FX",  # type: ignore[arg-type]
            )
