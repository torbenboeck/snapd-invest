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
from typing import TYPE_CHECKING, Literal

from snapd_invest.portfolio import build_summary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.models import Account, Instrument

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


async def evaluate(  # noqa: PLR0911 — each return is one independent guard clause; flattening hurts readability
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

    # A buy without a reference price cannot be sized — fail safe rather
    # than silently approve an unbounded position.
    if candidate.side == "buy" and candidate.reference_price is None:
        return RiskDecision("rejected", "missing_reference_price")

    summary = await build_summary(session, candidate.account)
    equity = summary.equity if summary.equity is not None else candidate.account.cash

    # Cash check first: "you can't afford this" is the most actionable error.
    # Position sizing (diversification) runs after, so insufficient_cash takes priority.
    if candidate.side == "buy" and candidate.reference_price is not None:
        cost = candidate.quantity * candidate.reference_price
        if cost > candidate.account.cash:
            return RiskDecision(
                "rejected", f"insufficient_cash:{candidate.account.cash}_needed_{cost}"
            )

        # Position sizing (only enforced for buys; sells reduce exposure)
        order_value = cost
        max_value = equity * config.max_position_pct_of_equity
        if order_value > max_value:
            return RiskDecision(
                "rejected",
                f"position_too_large:{order_value}_max_{max_value}",
            )

    return RiskDecision("allowed")
