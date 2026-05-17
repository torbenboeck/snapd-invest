"""Recommendation queue + lifecycle.

A `Recommendation` packages one or more agent-emitted signals for human review.
Lifecycle:

    pending → approved → executed   (user approves; execution succeeds)
    pending → modified → executed   (user approves with changes)
    pending → rejected              (user rejects)
    pending → expired               (user did not act before deadline)

Modifications are stored as a JSON delta against the original signals, so the
audit log preserves both the original proposal and the user's edits.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from snapd_invest.audit import record_event
from snapd_invest.execution import execute_signals
from snapd_invest.models import Recommendation, new_id
from snapd_invest.strategy import Signal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker import BrokerFactory
    from snapd_invest.clock import Clock
    from snapd_invest.promotion import PromotionGate
    from snapd_invest.risk import RiskConfig

DEFAULT_TTL_HOURS = 24


def _serialize_signals(signals: Sequence[Signal]) -> str:
    return json.dumps(
        [
            {
                "source": s.source,
                "account_id": s.account_id,
                "instrument_symbol": s.instrument_symbol,
                "instrument_exchange": s.instrument_exchange,
                "action": s.action,
                "quantity": str(s.quantity),
                "conviction": str(s.conviction),
                "rationale": s.rationale,
                "emitted_at": s.emitted_at.isoformat(),
                "correlation_id": s.correlation_id,
                "reference_price": (
                    str(s.reference_price) if s.reference_price is not None else None
                ),
            }
            for s in signals
        ]
    )


def _deserialize_signals(raw: str) -> list[Signal]:
    data = json.loads(raw)
    return [
        Signal(
            source=item["source"],
            account_id=item["account_id"],
            instrument_symbol=item["instrument_symbol"],
            instrument_exchange=item["instrument_exchange"],
            action=item["action"],
            quantity=Decimal(item["quantity"]),
            conviction=Decimal(item["conviction"]),
            rationale=item["rationale"],
            emitted_at=datetime.fromisoformat(item["emitted_at"]),
            correlation_id=item.get("correlation_id"),
            reference_price=(
                Decimal(item["reference_price"])
                if item.get("reference_price") is not None
                else None
            ),
        )
        for item in data
    ]


@dataclass(slots=True, frozen=True)
class SignalModification:
    """A user-provided override of an agent signal before execution."""

    instrument_symbol: str
    instrument_exchange: str
    quantity: Decimal | None = None
    skip: bool = False  # if True, exclude this signal from execution


async def create_recommendation(
    session: AsyncSession,
    clock: Clock,
    *,
    agent_id: str,
    signals: Sequence[Signal],
    rationale: str,
    correlation_id: str | None = None,
    ttl: timedelta | None = None,
) -> Recommendation:
    """Persist a new pending recommendation."""
    if not signals:
        raise ValueError("recommendation must include at least one signal")
    now = clock.now()
    rec = Recommendation(
        id=new_id(),
        agent_id=agent_id,
        signals=_serialize_signals(signals),
        rationale=rationale,
        status="pending",
        modifications=None,
        correlation_id=correlation_id,
        created_at=now,
        expires_at=now + (ttl or timedelta(hours=DEFAULT_TTL_HOURS)),
        resolved_at=None,
    )
    session.add(rec)
    await session.flush()

    await record_event(
        session,
        clock,
        event_type="recommendation_created",
        correlation_id=correlation_id,
        payload={
            "recommendation_id": rec.id,
            "agent_id": agent_id,
            "signal_count": len(signals),
        },
    )
    return rec


async def list_recommendations(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 100,
) -> Sequence[Recommendation]:
    stmt = select(Recommendation)
    if status is not None:
        stmt = stmt.where(Recommendation.status == status)
    stmt = stmt.order_by(Recommendation.created_at.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def get_recommendation(
    session: AsyncSession, recommendation_id: str
) -> Recommendation | None:
    return (
        await session.execute(select(Recommendation).where(Recommendation.id == recommendation_id))
    ).scalar_one_or_none()


async def expire_overdue(session: AsyncSession, clock: Clock) -> int:
    """Mark all pending recommendations whose deadline has passed as expired."""
    now = clock.now()
    stmt = select(Recommendation).where(
        Recommendation.status == "pending", Recommendation.expires_at < now
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for rec in rows:
        rec.status = "expired"
        rec.resolved_at = now
        await record_event(
            session,
            clock,
            event_type="recommendation_expired",
            correlation_id=rec.correlation_id,
            payload={"recommendation_id": rec.id},
        )
    await session.flush()
    return len(rows)


@dataclass(slots=True, frozen=True)
class ApprovalOutcome:
    recommendation: Recommendation
    execution_summaries: list[dict[str, Any]]


def _apply_modifications(
    signals: list[Signal], modifications: Sequence[SignalModification]
) -> list[Signal]:
    by_key = {(m.instrument_symbol, m.instrument_exchange): m for m in modifications}
    out: list[Signal] = []
    for s in signals:
        mod = by_key.get((s.instrument_symbol, s.instrument_exchange))
        if mod is None:
            out.append(s)
            continue
        if mod.skip:
            continue
        if mod.quantity is not None:
            out.append(
                Signal(
                    source=s.source,
                    account_id=s.account_id,
                    instrument_symbol=s.instrument_symbol,
                    instrument_exchange=s.instrument_exchange,
                    action=s.action,
                    quantity=mod.quantity,
                    conviction=s.conviction,
                    rationale=s.rationale + " [modified by user]",
                    emitted_at=s.emitted_at,
                    correlation_id=s.correlation_id,
                    reference_price=s.reference_price,
                )
            )
        else:
            out.append(s)
    return out


async def approve_and_execute(
    session: AsyncSession,
    clock: Clock,
    broker_factory: BrokerFactory,
    promotion_gate: PromotionGate,
    risk_config: RiskConfig,
    *,
    recommendation: Recommendation,
    modifications: Sequence[SignalModification] = (),
) -> ApprovalOutcome:
    """Mark recommendation approved (or modified), then run signals through execution."""
    # C-08: don't mutate-then-raise on expiry. The dedicated `expire_overdue`
    # sweep transitions the status; raising here leaves the row pending so the
    # caller's rollback semantics stay simple.
    if recommendation.expires_at < clock.now():
        raise ValueError(f"recommendation {recommendation.id} has expired")

    original_signals = _deserialize_signals(recommendation.signals)
    effective_signals = _apply_modifications(original_signals, modifications)
    has_modifications = bool(modifications)
    new_status = "modified" if has_modifications else "approved"
    modifications_json = (
        json.dumps([asdict(m) for m in modifications], default=str) if has_modifications else None
    )
    resolved_at = clock.now()

    # C-07: atomic, race-safe transition. Only succeeds if the row is still
    # pending. Two concurrent approvers see exactly one rowcount=1; the other
    # gets 0 and raises.
    result = await session.execute(
        update(Recommendation)
        .where(
            Recommendation.id == recommendation.id,
            Recommendation.status == "pending",
        )
        .values(
            status=new_status,
            modifications=modifications_json,
            resolved_at=resolved_at,
        )
    )
    rowcount: int = getattr(result, "rowcount", 0) or 0
    if rowcount == 0:
        raise ValueError(f"recommendation {recommendation.id} is not pending")

    # Sync in-memory ORM state with what the UPDATE just wrote.
    recommendation.status = new_status
    recommendation.modifications = modifications_json
    recommendation.resolved_at = resolved_at
    await session.flush()

    await record_event(
        session,
        clock,
        event_type="recommendation_resolved",
        correlation_id=recommendation.correlation_id,
        payload={
            "recommendation_id": recommendation.id,
            "status": recommendation.status,
            "modification_count": len(modifications),
        },
    )

    outcomes = await execute_signals(
        session, clock, broker_factory, promotion_gate, risk_config, effective_signals
    )
    recommendation.status = "executed"
    await session.flush()

    await record_event(
        session,
        clock,
        event_type="recommendation_executed",
        correlation_id=recommendation.correlation_id,
        payload={
            "recommendation_id": recommendation.id,
            "outcomes": [
                {
                    "instrument": f"{o.signal.instrument_symbol}@{o.signal.instrument_exchange}",
                    "gate_allowed": o.gate_allowed,
                    "gate_reason": o.gate_reason,
                    "order_id": o.order_id,
                    "order_status": o.order_status,
                }
                for o in outcomes
            ],
        },
    )

    return ApprovalOutcome(
        recommendation=recommendation,
        execution_summaries=[
            {
                "instrument": f"{o.signal.instrument_symbol}@{o.signal.instrument_exchange}",
                "action": o.signal.action,
                "quantity": str(o.signal.quantity),
                "gate_allowed": o.gate_allowed,
                "gate_reason": o.gate_reason,
                "order_id": o.order_id,
                "order_status": o.order_status,
            }
            for o in outcomes
        ],
    )


async def reject(
    session: AsyncSession,
    clock: Clock,
    *,
    recommendation: Recommendation,
    reason: str | None = None,
) -> Recommendation:
    if recommendation.status != "pending":
        raise ValueError(f"recommendation {recommendation.id} is not pending")
    recommendation.status = "rejected"
    recommendation.resolved_at = clock.now()
    await session.flush()

    await record_event(
        session,
        clock,
        event_type="recommendation_rejected",
        correlation_id=recommendation.correlation_id,
        payload={"recommendation_id": recommendation.id, "reason": reason},
    )
    return recommendation
