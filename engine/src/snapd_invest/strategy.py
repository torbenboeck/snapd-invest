"""Deterministic strategies.

A strategy is a pure-ish function over bar history + portfolio state -> Signals.
"Pure-ish" because we load bars from the DB inside `run()` for convenience, but
the *decision logic* is pure.

The flagship MVP strategy is `SMACrossoverStrategy`. Other strategies (grid,
RSI mean-revert, etc.) land in later PRs as additional classes in this file
(split out when this file grows past ~400 lines).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

from snapd_invest.data import load_recent_bars
from snapd_invest.indicators import crossover, sma

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.models import Account, Instrument

SignalAction = Literal["buy", "sell", "hold"]


@dataclass(slots=True, frozen=True)
class Signal:
    """A proposed action emitted by a strategy or agent.

    Has not been vetted by the risk gate yet.

    `reference_price` is the price at which the emitter was reasoning about the
    trade — bar close for strategies, last observed quote for agents. It is the
    risk gate's input for valuation (cash check, position sizing). It is *not*
    a broker constraint; market orders still go to the broker without a limit.

    `limit_price`, when set, makes this a limit order rather than a market
    order. Strategies don't set it (they emit market orders); manual
    placement via `POST /v1/orders` does.
    """

    source: str
    account_id: str
    instrument_symbol: str
    instrument_exchange: str
    action: SignalAction
    quantity: Decimal
    conviction: Decimal  # 0.0..1.0
    rationale: str
    emitted_at: datetime
    correlation_id: str | None = None
    reference_price: Decimal | None = None
    limit_price: Decimal | None = None


class IStrategy(Protocol):
    """Signal generator over current market + portfolio state."""

    name: str

    async def run(
        self,
        session: AsyncSession,
        *,
        account: Account,
        instrument: Instrument,
        emitted_at: datetime,
        correlation_id: str | None,
    ) -> list[Signal]: ...


# ----------------------------------------------------------------------------
# SMA Crossover
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SMACrossoverConfig:
    short_period: int = 50
    long_period: int = 200
    interval: str = "1d"
    quantity_per_signal: Decimal = Decimal("1")
    conviction: Decimal = Decimal("0.6")


class SMACrossoverStrategy:
    """Classic golden/death cross strategy.

    - Buy when short SMA crosses above long SMA.
    - Sell when short SMA crosses below long SMA.

    This is a baseline implementation. It is good for proving infrastructure;
    it is not a recommendation for live trading.
    """

    name = "sma_crossover"

    def __init__(self, config: SMACrossoverConfig | None = None) -> None:
        self._config = config or SMACrossoverConfig()
        if self._config.short_period >= self._config.long_period:
            raise ValueError("short_period must be < long_period")

    async def run(
        self,
        session: AsyncSession,
        *,
        account: Account,
        instrument: Instrument,
        emitted_at: datetime,
        correlation_id: str | None,
    ) -> list[Signal]:
        cfg = self._config
        bars = await load_recent_bars(
            session,
            instrument=instrument,
            interval=cfg.interval,
            limit=cfg.long_period + 5,
        )
        if len(bars) < cfg.long_period + 1:
            return []

        closes = [b.close for b in bars]
        short = sma(closes, cfg.short_period)
        long = sma(closes, cfg.long_period)
        direction = crossover(short, long)

        if direction == 0:
            return []

        action: SignalAction = "buy" if direction == 1 else "sell"
        rationale = (
            f"SMA{cfg.short_period}={short[-1]} crossed "
            f"{'above' if direction == 1 else 'below'} SMA{cfg.long_period}={long[-1]} "
            f"at close={closes[-1]}"
        )
        return [
            Signal(
                source=self.name,
                account_id=account.id,
                instrument_symbol=instrument.symbol,
                instrument_exchange=instrument.exchange,
                action=action,
                quantity=cfg.quantity_per_signal,
                conviction=cfg.conviction,
                rationale=rationale,
                emitted_at=emitted_at,
                correlation_id=correlation_id,
                reference_price=closes[-1],
            )
        ]
