"""Risk gate.

Every signal passes through here before becoming an order. The gate validates:

- **Kill switch** — global flag, when on rejects everything
- **Quantity** — positive non-zero
- **Instrument allowlist** — only trade configured instruments
- **Sell-quantity guard** — cannot sell more than the account holds (no shorting)
- **Position sizing** — max % of account equity per single position (buys only)
- **Cash** — buys must be affordable (buys only)
- **Daily loss circuit breaker** — if today's realized losses exceed
  `max_daily_loss_pct` of equity, halt trading

The gate is config-driven so unit tests can construct narrow scenarios. In
production, config comes from `Settings`.

This module is intentionally "boring" — explicit rules, no clever logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import select

from snapd_invest.models import Order, Position, Trade
from snapd_invest.portfolio import build_summary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import Clock
    from snapd_invest.models import Account, Instrument

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

GateOutcome = Literal["allowed", "rejected"]


@dataclass(slots=True, frozen=True)
class RiskConfig:
    """Configuration for the risk gate. Construct per account at startup."""

    max_position_pct_of_equity: Decimal = Decimal("0.20")  # 20% per position
    max_daily_loss_pct: Decimal = Decimal("0.05")  # 5% drawdown halts trading
    instrument_allowlist: frozenset[str] = field(default_factory=frozenset)
    kill_switch: bool = False


@dataclass(slots=True, frozen=True)
class RiskDecision:
    outcome: GateOutcome
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == "allowed"


@dataclass(slots=True, frozen=True)
class SignalCandidate:
    """The shape the gate consumes.

    `reference_price` is the price used for risk valuation (cash + position
    sizing). It is supplied by the emitter — bar close for strategies, last
    observed quote for agents — and is independent of the broker-side order
    type. A market order still goes to the broker without a limit price; the
    risk gate just needs a valuation reference.
    """

    account: Account
    instrument: Instrument
    side: Literal["buy", "sell"]
    quantity: Decimal
    reference_price: Decimal | None


async def evaluate(  # noqa: PLR0911 — each return is one independent guard clause
    session: AsyncSession,
    config: RiskConfig,
    candidate: SignalCandidate,
    *,
    clock: Clock | None = None,
) -> RiskDecision:
    """Run all gates against a single candidate. Return the first failing reason.

    `clock` is required when `config.max_daily_loss_pct > 0`; pass `None` only
    in narrow tests that explicitly opt out of the daily-loss check.
    """
    if config.kill_switch:
        return RiskDecision("rejected", "kill_switch_on")

    if candidate.quantity <= Decimal("0"):
        return RiskDecision("rejected", "non_positive_quantity")

    # Allowlist (empty allowlist = no allowlist enforcement)
    if config.instrument_allowlist:
        token = f"{candidate.instrument.symbol}@{candidate.instrument.exchange}"
        if token not in config.instrument_allowlist:
            return RiskDecision("rejected", f"instrument_not_allowed:{token}")

    # A buy without a reference price cannot be sized — fail safe rather
    # than silently approve an unbounded position.
    if candidate.side == "buy" and candidate.reference_price is None:
        return RiskDecision("rejected", "missing_reference_price")

    if candidate.side == "sell":
        sell_guard = await _check_sell_quantity(session, candidate)
        if sell_guard is not None:
            return sell_guard

    summary = await build_summary(session, candidate.account)
    if summary.equity is None:
        # Cash is always ≤ equity for a non-leveraged paper account, so this
        # fallback is conservative for position sizing. Surface it so the
        # operator can fix the missing-price root cause.
        log.warning("risk_equity_unknown_using_cash_proxy", account_id=candidate.account.id)
    equity = summary.equity if summary.equity is not None else candidate.account.cash

    if config.max_daily_loss_pct > Decimal("0") and clock is not None:
        daily_loss_check = await _check_daily_loss(session, clock, config, candidate, equity)
        if daily_loss_check is not None:
            return daily_loss_check

    if candidate.side == "buy":
        return _evaluate_buy(config, candidate, equity)

    return RiskDecision("allowed")


def _evaluate_buy(config: RiskConfig, candidate: SignalCandidate, equity: Decimal) -> RiskDecision:
    """Cash + position-size checks for buys. `reference_price` is guaranteed non-None here."""
    assert candidate.reference_price is not None
    cost = candidate.quantity * candidate.reference_price

    # Cash check first: "you can't afford this" is the most actionable error.
    if cost > candidate.account.cash:
        return RiskDecision("rejected", f"insufficient_cash:{candidate.account.cash}_needed_{cost}")

    max_value = equity * config.max_position_pct_of_equity
    if cost > max_value:
        return RiskDecision("rejected", f"position_too_large:{cost}_max_{max_value}")

    return RiskDecision("allowed")


async def _check_sell_quantity(
    session: AsyncSession, candidate: SignalCandidate
) -> RiskDecision | None:
    """Reject sells that exceed the held quantity (MVP forbids shorting)."""
    stmt = select(Position.quantity).where(
        Position.account_id == candidate.account.id,
        Position.instrument_id == candidate.instrument.id,
    )
    held = (await session.execute(stmt)).scalar_one_or_none() or Decimal("0")
    if held < candidate.quantity:
        return RiskDecision(
            "rejected", f"insufficient_position:held_{held}_needed_{candidate.quantity}"
        )
    return None


async def _check_daily_loss(
    session: AsyncSession,
    clock: Clock,
    config: RiskConfig,
    candidate: SignalCandidate,
    equity: Decimal,
) -> RiskDecision | None:
    """Reject if today's realized loss exceeds `max_daily_loss_pct` of equity.

    Realized P&L from sells today, computed using each position's current
    `avg_cost`. Approximation: if same-instrument buys occurred today, this
    overstates losses (conservative). Mark-to-market on open positions is not
    counted.
    """
    realized_pnl = await _today_realized_pnl(session, clock, candidate.account.id)
    if realized_pnl >= Decimal("0"):
        return None
    loss = -realized_pnl
    threshold = equity * config.max_daily_loss_pct
    if loss > threshold:
        return RiskDecision("rejected", f"daily_loss_exceeded:{loss}_max_{threshold}")
    return None


def _start_of_utc_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _today_realized_pnl(session: AsyncSession, clock: Clock, account_id: str) -> Decimal:
    """Sum signed P&L over today's sell trades for `account_id`. See `_check_daily_loss`."""
    since = _start_of_utc_day(clock.now())
    until = since + timedelta(days=1)
    stmt = (
        select(
            Trade.fill_price,
            Trade.fill_quantity,
            Trade.fees,
            Order.instrument_id,
        )
        .join(Order, Trade.order_id == Order.id)
        .where(
            Order.account_id == account_id,
            Order.side == "sell",
            Trade.occurred_at >= since,
            Trade.occurred_at < until,
        )
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return Decimal("0")

    avg_cost_by_instrument: dict[str, Decimal] = {}
    pnl = Decimal("0")
    for fill_price, fill_qty, fees, instrument_id in rows:
        if instrument_id not in avg_cost_by_instrument:
            avg_cost_stmt = select(Position.avg_cost).where(
                Position.account_id == account_id,
                Position.instrument_id == instrument_id,
            )
            avg_cost = (await session.execute(avg_cost_stmt)).scalar_one_or_none() or Decimal("0")
            avg_cost_by_instrument[instrument_id] = avg_cost
        avg_cost = avg_cost_by_instrument[instrument_id]
        pnl += (fill_price - avg_cost) * fill_qty - fees
    return pnl
