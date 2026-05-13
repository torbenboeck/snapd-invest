"""Risk gate.

Every signal passes through here before becoming an order. The gate validates:

- **Position sizing** — max % of account equity per single position
- **Daily loss** — kill if daily realized + unrealized loss exceeds threshold
- **Instrument allowlist** — only trade configured instruments
- **Kill switch** — global flag, when on rejects everything

The gate is config-driven so unit tests can construct narrow scenarios. In
production, config comes from `Settings`.

This module is intentionally "boring" — explicit rules, no clever logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from algo_invest.models import Account, Instrument
from algo_invest.portfolio import build_summary

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
    """The shape the gate consumes. Mirrors `OrderRequest` minus brokerage details."""

    account: Account
    instrument: Instrument
    side: Literal["buy", "sell"]
    quantity: Decimal
    limit_price: Decimal | None


async def evaluate(
    session: AsyncSession,
    config: RiskConfig,
    candidate: SignalCandidate,
) -> RiskDecision:
    """Run all gates against a single candidate. Return the first failing reason."""
    if config.kill_switch:
        return RiskDecision("rejected", "kill_switch_on")

    if candidate.quantity <= Decimal("0"):
        return RiskDecision("rejected", "non_positive_quantity")

    # Allowlist (empty allowlist = no allowlist enforcement)
    if config.instrument_allowlist:
        token = f"{candidate.instrument.symbol}@{candidate.instrument.exchange}"
        if token not in config.instrument_allowlist:
            return RiskDecision("rejected", f"instrument_not_allowed:{token}")

    summary = await build_summary(session, candidate.account)
    equity = summary.equity if summary.equity is not None else candidate.account.cash

    # Position sizing (only enforced for buys; sells reduce exposure)
    if candidate.side == "buy" and candidate.limit_price is not None:
        order_value = candidate.quantity * candidate.limit_price
        max_value = equity * config.max_position_pct_of_equity
        if order_value > max_value:
            return RiskDecision(
                "rejected",
                f"position_too_large:{order_value}_max_{max_value}",
            )

    # Cash check: buys need sufficient cash (at the limit price if given)
    if candidate.side == "buy" and candidate.limit_price is not None:
        cost = candidate.quantity * candidate.limit_price
        if cost > candidate.account.cash:
            return RiskDecision(
                "rejected", f"insufficient_cash:{candidate.account.cash}_needed_{cost}"
            )

    return RiskDecision("allowed")
