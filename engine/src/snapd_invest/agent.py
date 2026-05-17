"""LLM-powered analyst agents.

An agent has:
- A `personality` (risk tolerance, time horizon, conviction threshold)
- A set of `interests` (sectors, instruments to watch)
- A bound `account` (paper / sim / live)

At MVP, an agent runs synchronously: gather context → call LLM with a typed
schema → parse signals → wrap in a Recommendation. No tool-use loops yet.

The agent never executes orders directly. It produces Recommendations. The
user (or a future auto-approve gate) decides whether to execute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from snapd_invest.data import load_recent_bars
from snapd_invest.llm import ILlmProvider, LlmRequest
from snapd_invest.models import Account, Instrument, new_id
from snapd_invest.models import Agent as AgentModel
from snapd_invest.portfolio import build_summary
from snapd_invest.strategy import Signal

# Cadence at which agents read a reference price for risk valuation. Daily
# closes are what yfinance gives us at MVP and what the risk gate's
# position-sizing assumptions are tuned to.
AGENT_PRICE_INTERVAL = "1d"

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import Clock

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class Personality:
    """A bundle of dispositions for an LLM agent.

    These influence the prompt template, not deterministic behavior. Risk
    enforcement is still the risk gate's job, not the personality's.
    """

    name: str
    description: str
    risk_tolerance: Decimal  # 0.0 conservative .. 1.0 aggressive
    horizon_days: int
    min_conviction: Decimal  # ignore signals below this threshold
    interests: list[str] = field(default_factory=list)


CONSERVATIVE_VALUE = Personality(
    name="conservative_value",
    description=(
        "A patient value-oriented investor. Prefers well-established, dividend-paying "
        "companies trading below their intrinsic value. Avoids speculative bets. "
        "Holds for 6-24 months."
    ),
    risk_tolerance=Decimal("0.3"),
    horizon_days=180,
    min_conviction=Decimal("0.7"),
    interests=["bonds", "dividends", "value", "blue_chip"],
)

MOMENTUM_TRADER = Personality(
    name="momentum_trader",
    description=(
        "A trend-following trader. Buys assets in strong uptrends and rides them "
        "until the trend breaks. Cuts losers quickly. Holds for days to weeks."
    ),
    risk_tolerance=Decimal("0.7"),
    horizon_days=21,
    min_conviction=Decimal("0.55"),
    interests=["tech", "growth", "momentum"],
)


# ----------------------------------------------------------------------------
# Prompt schema for structured LLM output
# ----------------------------------------------------------------------------


AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["signals", "summary"],
    "properties": {
        "summary": {"type": "string"},
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "instrument_symbol",
                    "instrument_exchange",
                    "action",
                    "quantity",
                    "conviction",
                    "rationale",
                ],
                "properties": {
                    "instrument_symbol": {"type": "string"},
                    "instrument_exchange": {"type": "string"},
                    "action": {"enum": ["buy", "sell", "hold"]},
                    "quantity": {"type": "number"},
                    "conviction": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}


def _build_prompt(
    personality: Personality,
    *,
    cash: Decimal,
    positions_summary: str,
    watchlist: list[str],
) -> str:
    return f"""You are an investment analyst with the following personality:

Name: {personality.name}
Description: {personality.description}
Risk tolerance (0=conservative, 1=aggressive): {personality.risk_tolerance}
Time horizon: {personality.horizon_days} days
Interests: {", ".join(personality.interests)}

Current portfolio:
- Cash: {cash}
- Positions:
{positions_summary or "  (none)"}

Watchlist (instruments you may consider): {", ".join(watchlist) if watchlist else "(empty)"}

Produce a JSON object with this shape:
{{
  "summary": "One-paragraph reasoning about current market positioning.",
  "signals": [
    {{
      "instrument_symbol": "AAPL",
      "instrument_exchange": "NASDAQ",
      "action": "buy" | "sell" | "hold",
      "quantity": <number>,
      "conviction": <0..1>,
      "rationale": "Why this action."
    }}
  ]
}}

Rules:
- Only propose actions on instruments in the watchlist.
- Only emit a signal if your conviction is at least {personality.min_conviction}.
- Quantities must be positive numbers.
- If you have no actionable view, return signals=[] and explain in summary.
"""


# ----------------------------------------------------------------------------
# Agent runner
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class AgentRunResult:
    """Output of one agent invocation."""

    agent_name: str
    summary: str
    signals: list[Signal]


def _format_positions(positions: Sequence[Any]) -> str:
    lines = []
    for p in positions:
        lines.append(
            f"  - {p.instrument_symbol}@{p.instrument_exchange}: "
            f"qty={p.quantity}, avg_cost={p.avg_cost}, last={p.last_price or 'n/a'}, "
            f"tag={p.tag}"
        )
    return "\n".join(lines)


async def run_agent(
    session: AsyncSession,
    clock: Clock,
    llm: ILlmProvider,
    *,
    agent: AgentModel,
    personality: Personality,
    watchlist: list[Instrument],
    correlation_id: str | None = None,
) -> AgentRunResult:
    """Execute one analyst run for the agent. Produces signals; does NOT persist them."""
    account = (
        await session.execute(select(Account).where(Account.id == agent.account_id))
    ).scalar_one()

    summary = await build_summary(session, account)
    prompt = _build_prompt(
        personality,
        cash=account.cash,
        positions_summary=_format_positions(summary.positions),
        watchlist=[f"{i.symbol}@{i.exchange}" for i in watchlist],
    )

    request = LlmRequest(prompt=prompt, json_schema=AGENT_OUTPUT_SCHEMA, temperature=0.2)
    response = await llm.generate(request)
    if response.parsed is None:
        raise ValueError("LLM did not return parseable JSON for an agent run")

    summary_text = str(response.parsed.get("summary", ""))
    raw_signals = response.parsed.get("signals", [])
    if not isinstance(raw_signals, list):
        raise ValueError("LLM 'signals' field must be a list")

    emitted_at = clock.now()
    watchlist_keys = {f"{i.symbol}@{i.exchange}" for i in watchlist}

    # Reference price per watchlist instrument — latest daily close if
    # bar data exists. Used by the risk gate for valuation.
    reference_prices: dict[str, Decimal] = {}
    for i in watchlist:
        bars = await load_recent_bars(session, instrument=i, interval=AGENT_PRICE_INTERVAL, limit=1)
        if bars:
            reference_prices[f"{i.symbol}@{i.exchange}"] = bars[-1].close

    signals: list[Signal] = []
    for raw in raw_signals:
        if not isinstance(raw, dict):
            log.warning("agent_signal_skipped_non_dict", agent=agent.name)
            continue
        try:
            instrument_symbol = str(raw["instrument_symbol"])
            instrument_exchange = str(raw["instrument_exchange"])
            action = str(raw["action"])
            quantity = Decimal(str(raw["quantity"]))
            conviction = Decimal(str(raw["conviction"]))
            rationale = str(raw["rationale"])
        except (KeyError, ValueError) as exc:
            log.warning("agent_signal_skipped_invalid", agent=agent.name, error=str(exc))
            continue

        token = f"{instrument_symbol}@{instrument_exchange}"
        if token not in watchlist_keys:
            log.info("agent_signal_skipped_off_watchlist", agent=agent.name, instrument=token)
            continue
        if action not in {"buy", "sell", "hold"}:
            log.warning("agent_signal_skipped_bad_action", agent=agent.name, action=action)
            continue
        if conviction < personality.min_conviction:
            log.info(
                "agent_signal_skipped_low_conviction",
                agent=agent.name,
                conviction=str(conviction),
                threshold=str(personality.min_conviction),
            )
            continue
        if quantity <= Decimal("0") and action != "hold":
            log.warning("agent_signal_skipped_non_positive_quantity", agent=agent.name)
            continue

        signals.append(
            Signal(
                source=f"agent:{agent.name}",
                account_id=account.id,
                instrument_symbol=instrument_symbol,
                instrument_exchange=instrument_exchange,
                # action is validated against {"buy","sell","hold"} above, but
                # mypy can't narrow `str` to the SignalAction Literal here.
                action=action,  # type: ignore[arg-type]
                quantity=quantity,
                conviction=conviction,
                rationale=rationale,
                emitted_at=emitted_at,
                correlation_id=correlation_id,
                reference_price=reference_prices.get(token),
            )
        )

    return AgentRunResult(agent_name=agent.name, summary=summary_text, signals=signals)


# ----------------------------------------------------------------------------
# Helper to register the default MVP agent
# ----------------------------------------------------------------------------


async def ensure_default_agent(
    session: AsyncSession,
    clock: Clock,
    *,
    account: Account,
    personality: Personality = CONSERVATIVE_VALUE,
) -> AgentModel:
    """Get or create an enabled agent bound to the given account with the personality."""
    stmt = select(AgentModel).where(AgentModel.name == personality.name)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    agent = AgentModel(
        id=new_id(),
        name=personality.name,
        personality=personality.name,
        interests=json.dumps(personality.interests),
        environment=account.account_type,
        account_id=account.id,
        enabled=True,
        created_at=clock.now(),
    )
    session.add(agent)
    await session.flush()
    return agent
