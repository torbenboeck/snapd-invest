"""Market data — fetching, caching, querying bars.

At MVP we support two free sources:
- `yfinance` for stocks and ETFs (daily and intraday bars)
- `ccxt` for crypto exchanges (1m to daily)

A real-time WebSocket layer is deferred. Polling is enough for an MVP where the
micro-trader runs every minute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from algo_invest.clock import Clock
from algo_invest.models import Bar, Instrument, new_id


@dataclass(slots=True, frozen=True)
class BarData:
    """Stack-agnostic bar value object. Use this in strategies, not the ORM type."""

    instrument_symbol: str
    interval: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class IMarketDataProvider(Protocol):
    """Pulls bars for an instrument from an external source."""

    async def fetch_bars(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: str,
        limit: int,
    ) -> Sequence[BarData]:
        """Return up to `limit` most recent bars, oldest first."""
        ...


class FakeMarketDataProvider:
    """Returns canned bars for tests. Pre-populated via `seed(...)`."""

    def __init__(self) -> None:
        self._bars: dict[tuple[str, str, str], list[BarData]] = {}

    def seed(self, *, symbol: str, exchange: str, interval: str, bars: list[BarData]) -> None:
        self._bars[(symbol, exchange, interval)] = list(bars)

    async def fetch_bars(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: str,
        limit: int,
    ) -> Sequence[BarData]:
        bars = self._bars.get((symbol, exchange, interval), [])
        return bars[-limit:]


# ----------------------------------------------------------------------------
# Persistence helpers
# ----------------------------------------------------------------------------


async def ensure_instrument(
    session: AsyncSession,
    *,
    symbol: str,
    exchange: str,
    instrument_type: str,
    currency: str,
    tick_size: Decimal = Decimal("0.01"),
) -> Instrument:
    """Get or create an instrument by (symbol, exchange)."""
    stmt = select(Instrument).where(
        Instrument.symbol == symbol, Instrument.exchange == exchange
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    instrument = Instrument(
        id=new_id(),
        symbol=symbol,
        exchange=exchange,
        instrument_type=instrument_type,
        currency=currency,
        tick_size=tick_size,
    )
    session.add(instrument)
    await session.flush()
    return instrument


async def upsert_bars(
    session: AsyncSession,
    *,
    instrument: Instrument,
    bars: Sequence[BarData],
    source: str,
) -> int:
    """Insert bars that don't already exist. Returns the count of new rows."""
    inserted = 0
    for bar in bars:
        stmt = select(Bar).where(
            Bar.instrument_id == instrument.id,
            Bar.interval == bar.interval,
            Bar.timestamp == bar.timestamp,
        )
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            continue
        session.add(
            Bar(
                id=new_id(),
                instrument_id=instrument.id,
                interval=bar.interval,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                source=source,
            )
        )
        inserted += 1
    await session.flush()
    return inserted


async def load_recent_bars(
    session: AsyncSession,
    *,
    instrument: Instrument,
    interval: str,
    limit: int,
) -> list[BarData]:
    """Load the most recent `limit` bars from the DB, oldest first."""
    stmt = (
        select(Bar)
        .where(Bar.instrument_id == instrument.id, Bar.interval == interval)
        .order_by(Bar.timestamp.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()
    return [
        BarData(
            instrument_symbol=instrument.symbol,
            interval=b.interval,
            timestamp=b.timestamp,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in rows
    ]


# ----------------------------------------------------------------------------
# Refresh — orchestrates provider + persistence
# ----------------------------------------------------------------------------


async def refresh_bars(
    session: AsyncSession,
    clock: Clock,
    provider: IMarketDataProvider,
    *,
    instrument: Instrument,
    interval: str,
    limit: int,
    source: str,
) -> int:
    """Fetch bars from the provider and persist any new ones. Returns count inserted."""
    _ = clock  # reserved for future use (e.g. last-refresh timestamp)
    bars = await provider.fetch_bars(
        symbol=instrument.symbol,
        exchange=instrument.exchange,
        interval=interval,
        limit=limit,
    )
    return await upsert_bars(session, instrument=instrument, bars=bars, source=source)
