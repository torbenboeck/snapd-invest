"""Per-tick orchestration.

This module owns "what happens for one MicroTrader / agent / expire tick".
Both the FastAPI route handlers and the APScheduler-driven jobs delegate
here so there is exactly one code path per concern.

Boundary discipline:
  * No HTTP.
  * No APScheduler.
  * Takes its dependencies as arguments — session, clock, broker, llm,
    risk_config, etc. Does not pull them from app.state or env.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from snapd_invest.agent import CONSERVATIVE_VALUE, ensure_default_agent, run_agent
from snapd_invest.execution import execute_signals
from snapd_invest.recommendation import create_recommendation, expire_overdue
from snapd_invest.strategy import SMACrossoverStrategy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.agent import Personality
    from snapd_invest.broker import BrokerFactory
    from snapd_invest.clock import Clock
    from snapd_invest.llm import ILlmProvider
    from snapd_invest.models import Account, Instrument
    from snapd_invest.promotion import PromotionGate
    from snapd_invest.risk import RiskConfig
    from snapd_invest.strategy import Signal, SMACrossoverConfig


def parse_watchlist_entry(entry: str) -> tuple[str, str]:
    """Parse one 'SYMBOL@EXCHANGE' string into a (symbol, exchange) tuple.

    Whitespace around the separator and at the ends is stripped. An entry
    is rejected if it has no '@', an empty symbol, or an empty exchange —
    fail-fast at startup is preferable to a silent skip at tick time.
    """
    if "@" not in entry:
        raise ValueError(f"watchlist entry must be in SYMBOL@EXCHANGE format, got {entry!r}")
    symbol, exchange = (part.strip() for part in entry.split("@", maxsplit=1))
    if not symbol:
        raise ValueError(f"watchlist entry has empty symbol: {entry!r}")
    if not exchange:
        raise ValueError(f"watchlist entry has empty exchange: {entry!r}")
    return symbol, exchange


@dataclass(slots=True, frozen=True)
class MicroTraderOutcome:
    """Result of one MicroTrader tick for one instrument."""

    signals: list[Signal]
    execution_summaries: list[dict[str, Any]]


async def run_microtrader_once(
    session: AsyncSession,
    clock: Clock,
    broker_factory: BrokerFactory,
    promotion_gate: PromotionGate,
    risk_config: RiskConfig,
    *,
    account: Account,
    instrument: Instrument,
    strategy_config: SMACrossoverConfig | None = None,
    correlation_id: str | None = None,
) -> MicroTraderOutcome:
    """Run one MicroTrader tick for a single instrument.

    Called by `POST /v1/run-once` and by the scheduled MicroTrader job. The
    function loads bars, runs the strategy, sends any signals through the
    promotion gate, risk gate, and broker. It does NOT commit the session —
    the caller owns the transaction boundary.
    """
    strategy = SMACrossoverStrategy(strategy_config)
    signals = await strategy.run(
        session,
        account=account,
        instrument=instrument,
        emitted_at=clock.now(),
        correlation_id=correlation_id,
    )
    outcomes = await execute_signals(
        session, clock, broker_factory, promotion_gate, risk_config, signals
    )
    return MicroTraderOutcome(
        signals=list(signals),
        execution_summaries=[
            {
                "instrument": f"{o.signal.instrument_symbol}@{o.signal.instrument_exchange}",
                "gate_allowed": o.gate_allowed,
                "gate_reason": o.gate_reason,
                "order_id": o.order_id,
                "order_status": o.order_status,
            }
            for o in outcomes
        ],
    )


@dataclass(slots=True, frozen=True)
class AgentOutcome:
    """Result of one agent run for one instrument."""

    agent_name: str
    summary: str
    recommendation_id: str | None


async def run_agent_once(
    session: AsyncSession,
    clock: Clock,
    llm: ILlmProvider,
    *,
    account: Account,
    instrument: Instrument,
    personality: Personality = CONSERVATIVE_VALUE,
    correlation_id: str | None = None,
) -> AgentOutcome:
    """Run one agent tick: ensure the agent exists, run it against the
    instrument, and package any resulting signals as a Recommendation.

    Does NOT commit the session — the caller owns the transaction boundary.
    """
    agent = await ensure_default_agent(session, clock, account=account, personality=personality)
    result = await run_agent(
        session,
        clock,
        llm,
        agent=agent,
        personality=personality,
        watchlist=[instrument],
        correlation_id=correlation_id,
    )
    recommendation_id: str | None = None
    if result.signals:
        rec = await create_recommendation(
            session,
            clock,
            agent_id=agent.id,
            signals=result.signals,
            rationale=result.summary,
            correlation_id=correlation_id,
        )
        recommendation_id = rec.id
    return AgentOutcome(
        agent_name=result.agent_name,
        summary=result.summary,
        recommendation_id=recommendation_id,
    )


async def expire_overdue_recommendations(
    session: AsyncSession,
    clock: Clock,
) -> int:
    """Sweep pending recommendations whose deadline has passed and mark them
    expired. Returns the number of rows expired.

    Thin wrapper over `recommendation.expire_overdue`. Exists in the pipeline
    module so the scheduler imports a single coherent surface.
    """
    return await expire_overdue(session, clock)
