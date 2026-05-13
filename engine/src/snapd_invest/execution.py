"""Execution pipeline: Signal -> RiskGate -> Order.

This module orchestrates the path from a strategy/agent signal to a placed
order. Every step records an audit event so the full decision history is
reconstructible.

The MicroTrader pipeline auto-executes signals from deterministic strategies.
Agent signals go through `recommendation.py` and require human approval before
reaching this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snapd_invest.audit import record_event
from snapd_invest.broker import IBroker, OrderRequest
from snapd_invest.clock import Clock
from snapd_invest.models import Account, Instrument
from snapd_invest.risk import RiskConfig, SignalCandidate, evaluate
from snapd_invest.strategy import Signal


@dataclass(slots=True, frozen=True)
class ExecutionOutcome:
    """Result of trying to execute a single signal."""

    signal: Signal
    gate_allowed: bool
    gate_reason: str | None
    order_id: str | None
    order_status: str | None


def _make_idempotency_key(signal: Signal) -> str:
    """Deterministic key from signal content. Prevents duplicate fills on retry."""
    raw = (
        f"{signal.source}|{signal.account_id}|"
        f"{signal.instrument_symbol}@{signal.instrument_exchange}|"
        f"{signal.action}|{signal.quantity}|{signal.emitted_at.isoformat()}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def _load_instrument(
    session: AsyncSession, *, symbol: str, exchange: str
) -> Instrument:
    stmt = select(Instrument).where(
        Instrument.symbol == symbol, Instrument.exchange == exchange
    )
    return (await session.execute(stmt)).scalar_one()


async def _load_account(session: AsyncSession, account_id: str) -> Account:
    stmt = select(Account).where(Account.id == account_id)
    return (await session.execute(stmt)).scalar_one()


async def execute_signal(
    session: AsyncSession,
    clock: Clock,
    broker: IBroker,
    risk_config: RiskConfig,
    signal: Signal,
) -> ExecutionOutcome:
    """Send a single signal through the risk gate and (if allowed) the broker.

    Records an audit event at each step.
    """
    instrument = await _load_instrument(
        session, symbol=signal.instrument_symbol, exchange=signal.instrument_exchange
    )
    account = await _load_account(session, signal.account_id)

    await record_event(
        session,
        clock,
        event_type="signal_emitted",
        correlation_id=signal.correlation_id,
        payload={
            "source": signal.source,
            "instrument": f"{signal.instrument_symbol}@{signal.instrument_exchange}",
            "action": signal.action,
            "quantity": str(signal.quantity),
            "conviction": str(signal.conviction),
            "rationale": signal.rationale,
        },
    )

    if signal.action == "hold":
        return ExecutionOutcome(
            signal=signal, gate_allowed=True, gate_reason=None, order_id=None, order_status="hold"
        )

    decision = await evaluate(
        session,
        risk_config,
        SignalCandidate(
            account=account,
            instrument=instrument,
            side=signal.action,
            quantity=signal.quantity,
            limit_price=None,
        ),
    )
    await record_event(
        session,
        clock,
        event_type="risk_decision",
        correlation_id=signal.correlation_id,
        payload={
            "source": signal.source,
            "instrument": f"{signal.instrument_symbol}@{signal.instrument_exchange}",
            "outcome": decision.outcome,
            "reason": decision.reason,
        },
    )

    if not decision.allowed:
        return ExecutionOutcome(
            signal=signal,
            gate_allowed=False,
            gate_reason=decision.reason,
            order_id=None,
            order_status=None,
        )

    result = await broker.place_order(
        session,
        OrderRequest(
            account=account,
            instrument=instrument,
            side=signal.action,
            quantity=signal.quantity,
            limit_price=None,
            source=signal.source,
            idempotency_key=_make_idempotency_key(signal),
            correlation_id=signal.correlation_id,
        ),
    )
    await record_event(
        session,
        clock,
        event_type="order_placed",
        correlation_id=signal.correlation_id,
        payload={
            "order_id": result.order.id,
            "status": result.order.status,
            "idempotent_replay": result.was_idempotent_replay,
            "trade_count": len(result.trades),
        },
    )

    return ExecutionOutcome(
        signal=signal,
        gate_allowed=True,
        gate_reason=None,
        order_id=result.order.id,
        order_status=result.order.status,
    )


async def execute_signals(
    session: AsyncSession,
    clock: Clock,
    broker: IBroker,
    risk_config: RiskConfig,
    signals: Sequence[Signal],
) -> list[ExecutionOutcome]:
    """Run a batch of signals through the pipeline. Returns per-signal outcomes."""
    outcomes: list[ExecutionOutcome] = []
    for signal in signals:
        outcomes.append(await execute_signal(session, clock, broker, risk_config, signal))
    return outcomes
