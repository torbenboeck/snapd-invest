# T-003 Scheduler wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire APScheduler into the FastAPI lifespan so the MicroTrader, the agent, and recommendation-expiry run autonomously inside the engine process, sharing one code path with the existing `POST /v1/run-once` and `POST /v1/agents/run` routes.

**Architecture:** New `pipeline.py` module owns "what happens per tick" — three orchestration functions (`run_microtrader_once`, `run_agent_once`, `expire_overdue_recommendations`). Existing routes and new scheduler closures both delegate there. `scheduler.py` gains a `build_default_jobs` factory and an error-event listener; the FastAPI `lifespan` starts the scheduler when `Settings.scheduler_enabled=True` and shuts it down cleanly.

**Tech stack:** Python 3.12 · FastAPI · APScheduler (`AsyncIOScheduler`) · SQLAlchemy 2.x async · Pydantic V2 Settings · pytest + pytest-asyncio · structlog.

**Spec:** [`docs/specs/T-003-scheduler-wiring.md`](../specs/T-003-scheduler-wiring.md).

**Branch:** `feature/T-003-scheduler-wiring` (already created from main, contains the spec commit `248cdc8`).

---

## File structure (locked-in)

| File | Action | Responsibility |
|---|---|---|
| `engine/src/snapd_invest/config.py` | Modify | Add scheduler + watchlist Settings fields |
| `engine/src/snapd_invest/pipeline.py` | **Create** | Orchestration: three per-tick functions + `parse_watchlist_entry` helper |
| `engine/src/snapd_invest/scheduler.py` | Modify | Add `build_default_jobs` factory + `EVENT_JOB_ERROR` listener |
| `engine/src/snapd_invest/api.py` | Modify | Refactor 2 routes to call pipeline; extend lifespan to start/stop scheduler |
| `engine/tests/unit/test_config.py` | Modify | Cover new Settings fields |
| `engine/tests/unit/test_pipeline.py` | **Create** | Unit tests for the three pipeline functions + parser |
| `engine/tests/unit/test_scheduler.py` | **Create** | Scheduler-integration test + `build_default_jobs` tests |
| `engine/.env.example` | Modify | Document new env vars |
| `docs/architecture/module-map.md` | Modify | Add `pipeline.py` to the module table |

`pipeline.py` is intentionally the only new production module — keeps the flat module layout per `engine/CLAUDE.md`.

---

## Working rules during execution

- Activate the engine venv via `uv run …` for every test/lint/type/format command. Do **not** `cd engine` repeatedly — use `uv --directory engine run …` or set cwd once at task start and stay there.
- After every implementation step, the suite must be green: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`. If any goes red, fix before moving on.
- Conventional commits: `feat(scope)`, `test(scope)`, `refactor(scope)`. Reference `T-003` in the commit body.
- One commit per task (not per step) — the steps inside a task are TDD micro-iterations, the commit captures the finished increment.

---

## Task 1: Add scheduler + watchlist fields to `Settings`

**Files:**
- Modify: `engine/src/snapd_invest/config.py`
- Modify: `engine/tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests for the new fields**

Add at the end of `engine/tests/unit/test_config.py`:

```python
def test_scheduler_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.scheduler_enabled is True
    assert s.microtrader_interval_minutes == 1
    assert s.agent_interval_minutes == 30
    assert s.recommendation_expire_interval_minutes == 5
    assert s.default_account_name == "paper"
    assert s.watchlist == ["AAPL@NASDAQ"]


def test_scheduler_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNAPDINVEST_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SNAPDINVEST_MICROTRADER_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("SNAPDINVEST_AGENT_INTERVAL_MINUTES", "15")
    monkeypatch.setenv("SNAPDINVEST_WATCHLIST", "AAPL@NASDAQ,BTC-USD@BINANCE")
    monkeypatch.setenv("SNAPDINVEST_DEFAULT_ACCOUNT_NAME", "sim-account")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.scheduler_enabled is False
    assert s.microtrader_interval_minutes == 5
    assert s.agent_interval_minutes == 15
    assert s.watchlist == ["AAPL@NASDAQ", "BTC-USD@BINANCE"]
    assert s.default_account_name == "sim-account"


def test_scheduler_interval_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, microtrader_interval_minutes=0)  # type: ignore[call-arg]
```

At the top of the file, add the `ValidationError` import if missing:

```python
from pydantic import ValidationError
```

- [ ] **Step 2: Run to confirm failure**

```
uv --directory engine run pytest tests/unit/test_config.py -v -k "scheduler"
```

Expected: 3 failures with `AttributeError: 'Settings' object has no attribute 'scheduler_enabled'` (or similar — fields don't exist yet).

- [ ] **Step 3: Implement the fields in `config.py`**

Append to the `Settings` class body in `engine/src/snapd_invest/config.py` (after `api_port`):

```python
    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------
    scheduler_enabled: bool = Field(
        default=True,
        description="Start the background scheduler on app startup. Disable for tests/dev.",
    )
    microtrader_interval_minutes: int = Field(
        default=1,
        ge=1,
        description="Minutes between MicroTrader ticks.",
    )
    agent_interval_minutes: int = Field(
        default=30,
        ge=1,
        description="Minutes between agent runs.",
    )
    recommendation_expire_interval_minutes: int = Field(
        default=5,
        ge=1,
        description="Minutes between recommendation-expiry sweeps.",
    )

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------
    watchlist: list[str] = Field(
        default_factory=lambda: ["AAPL@NASDAQ"],
        description=(
            "Instruments the scheduler runs strategies/agents against. "
            "Comma-separated SYMBOL@EXCHANGE entries when set via env."
        ),
    )
    default_account_name: str = Field(
        default="paper",
        description="Account name the scheduled jobs operate against.",
    )
```

- [ ] **Step 4: Run to confirm tests pass**

```
uv --directory engine run pytest tests/unit/test_config.py -v
```

Expected: all tests pass (existing + 3 new).

- [ ] **Step 5: Run lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: All checks passed / Success.

- [ ] **Step 6: Commit**

```
git add engine/src/snapd_invest/config.py engine/tests/unit/test_config.py
git commit -m "feat(config): add scheduler + watchlist Settings fields

Adds scheduler_enabled, microtrader_interval_minutes,
agent_interval_minutes, recommendation_expire_interval_minutes,
watchlist, default_account_name to Settings, with env-override
support and 'minutes >= 1' validation.

Task: T-003"
```

---

## Task 2: Create `pipeline.py` with `parse_watchlist_entry`

**Files:**
- Create: `engine/src/snapd_invest/pipeline.py`
- Create: `engine/tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing parser tests**

Create `engine/tests/unit/test_pipeline.py`:

```python
"""Tests for `snapd_invest.pipeline`."""

from __future__ import annotations

import pytest

from snapd_invest.pipeline import parse_watchlist_entry


class TestParseWatchlistEntry:
    def test_valid_entry(self) -> None:
        symbol, exchange = parse_watchlist_entry("AAPL@NASDAQ")
        assert symbol == "AAPL"
        assert exchange == "NASDAQ"

    def test_dashes_in_symbol(self) -> None:
        symbol, exchange = parse_watchlist_entry("BTC-USD@BINANCE")
        assert symbol == "BTC-USD"
        assert exchange == "BINANCE"

    def test_strips_whitespace(self) -> None:
        symbol, exchange = parse_watchlist_entry("  AAPL @ NASDAQ  ")
        assert symbol == "AAPL"
        assert exchange == "NASDAQ"

    def test_missing_at_sign(self) -> None:
        with pytest.raises(ValueError, match="SYMBOL@EXCHANGE"):
            parse_watchlist_entry("AAPL")

    def test_empty_symbol(self) -> None:
        with pytest.raises(ValueError, match="empty symbol"):
            parse_watchlist_entry("@NASDAQ")

    def test_empty_exchange(self) -> None:
        with pytest.raises(ValueError, match="empty exchange"):
            parse_watchlist_entry("AAPL@")
```

- [ ] **Step 2: Run to confirm failure**

```
uv --directory engine run pytest tests/unit/test_pipeline.py -v
```

Expected: `ModuleNotFoundError: No module named 'snapd_invest.pipeline'`.

- [ ] **Step 3: Create `pipeline.py` with the parser**

Create `engine/src/snapd_invest/pipeline.py`:

```python
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


def parse_watchlist_entry(entry: str) -> tuple[str, str]:
    """Parse one 'SYMBOL@EXCHANGE' string into a (symbol, exchange) tuple.

    Whitespace around the separator and at the ends is stripped. An entry
    is rejected if it has no '@', an empty symbol, or an empty exchange —
    fail-fast at startup is preferable to a silent skip at tick time.
    """
    if "@" not in entry:
        raise ValueError(
            f"watchlist entry must be in SYMBOL@EXCHANGE format, got {entry!r}"
        )
    symbol, exchange = (part.strip() for part in entry.split("@", maxsplit=1))
    if not symbol:
        raise ValueError(f"watchlist entry has empty symbol: {entry!r}")
    if not exchange:
        raise ValueError(f"watchlist entry has empty exchange: {entry!r}")
    return symbol, exchange
```

- [ ] **Step 4: Run to confirm tests pass**

```
uv --directory engine run pytest tests/unit/test_pipeline.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean.

- [ ] **Step 6: Commit**

```
git add engine/src/snapd_invest/pipeline.py engine/tests/unit/test_pipeline.py
git commit -m "feat(pipeline): add pipeline.py with parse_watchlist_entry

Skeleton of the per-tick orchestration module. The parser raises
ValueError with a clear message on malformed entries so the
scheduler fails fast at startup, not at tick time.

Task: T-003"
```

---

## Task 3: Add `run_microtrader_once` to `pipeline.py`

**Files:**
- Modify: `engine/src/snapd_invest/pipeline.py`
- Modify: `engine/tests/unit/test_pipeline.py`

- [ ] **Step 1: Read existing helpers to align signatures**

Open `engine/src/snapd_invest/execution.py` and confirm:
- `async def execute_signals(session, clock, broker, risk_config, signals) -> list[ExecutionOutcome]`
- `ExecutionOutcome` shape (has `signal`, `gate_allowed`, `gate_reason`, `order_id`, `order_status`).

No code change in this step — just confirm signatures so the test you write next is accurate.

- [ ] **Step 2: Write the failing tests**

Append to `engine/tests/unit/test_pipeline.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from snapd_invest.broker import PaperBroker
from snapd_invest.data import BarData, ensure_instrument, upsert_bars
from snapd_invest.pipeline import run_microtrader_once
from snapd_invest.portfolio import create_account
from snapd_invest.risk import RiskConfig
from snapd_invest.strategy import SMACrossoverConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import FakeClock


async def _seed_aapl_with_golden_cross_bars(
    session: AsyncSession,
) -> object:
    """Helper: ensures an AAPL instrument with bars that produce a golden
    cross at the last index for short_period=2, long_period=5."""
    instrument = await ensure_instrument(
        session,
        symbol="AAPL",
        exchange="NASDAQ",
        instrument_type="stock",
        currency="USD",
    )
    closes = (
        [Decimal("100")] * 10
        + [Decimal("90")] * 5
        + [Decimal("80"), Decimal("80"), Decimal("80")]
        + [Decimal("200")]
    )
    bars = [
        BarData(
            instrument_symbol="AAPL",
            interval="1d",
            timestamp=datetime(2026, 4, 1, tzinfo=UTC).replace(day=i + 1),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=Decimal("1000"),
        )
        for i, c in enumerate(closes)
    ]
    await upsert_bars(session, instrument=instrument, bars=bars, source="test")
    return instrument


class TestRunMicroTraderOnce:
    async def test_emits_buy_signal_on_golden_cross(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("100000")
        )
        instrument = await _seed_aapl_with_golden_cross_bars(db_session)
        broker = PaperBroker(fake_clock)

        outcome = await run_microtrader_once(
            db_session,
            fake_clock,
            broker,
            RiskConfig(),
            account=account,
            instrument=instrument,
            strategy_config=SMACrossoverConfig(short_period=2, long_period=5),
        )

        assert len(outcome.signals) == 1
        assert outcome.signals[0].action == "buy"
        assert len(outcome.execution_summaries) == 1
        assert outcome.execution_summaries[0]["gate_allowed"] is True

    async def test_no_signal_when_no_bars(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        broker = PaperBroker(fake_clock)

        outcome = await run_microtrader_once(
            db_session,
            fake_clock,
            broker,
            RiskConfig(),
            account=account,
            instrument=instrument,
            strategy_config=SMACrossoverConfig(short_period=2, long_period=5),
        )

        assert outcome.signals == []
        assert outcome.execution_summaries == []

    async def test_risk_rejects_insufficient_cash(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        # Tiny initial cash; the golden-cross bars cost more than account has.
        account = await create_account(
            db_session, fake_clock, name="paper", initial_cash=Decimal("10")
        )
        instrument = await _seed_aapl_with_golden_cross_bars(db_session)
        broker = PaperBroker(fake_clock)

        outcome = await run_microtrader_once(
            db_session,
            fake_clock,
            broker,
            RiskConfig(),
            account=account,
            instrument=instrument,
            strategy_config=SMACrossoverConfig(short_period=2, long_period=5),
        )

        assert len(outcome.signals) == 1
        assert outcome.execution_summaries[0]["gate_allowed"] is False
        assert "insufficient_cash" in outcome.execution_summaries[0]["gate_reason"]
```

- [ ] **Step 3: Run to confirm failure**

```
uv --directory engine run pytest tests/unit/test_pipeline.py::TestRunMicroTraderOnce -v
```

Expected: 3 failures with `ImportError: cannot import name 'run_microtrader_once'`.

- [ ] **Step 4: Implement `run_microtrader_once`**

Add to `engine/src/snapd_invest/pipeline.py` (after the parser, before the module ends):

```python
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from snapd_invest.execution import execute_signals
from snapd_invest.strategy import SMACrossoverConfig, SMACrossoverStrategy, Signal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.broker import IBroker
    from snapd_invest.clock import Clock
    from snapd_invest.models import Account, Instrument
    from snapd_invest.risk import RiskConfig


@dataclass(slots=True, frozen=True)
class MicroTraderOutcome:
    """Result of one MicroTrader tick for one instrument."""

    signals: list[Signal]
    execution_summaries: list[dict[str, Any]]


async def run_microtrader_once(
    session: AsyncSession,
    clock: Clock,
    broker: IBroker,
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
    risk gate, and persists orders via the broker. It does NOT commit the
    session — the caller owns the transaction boundary.
    """
    strategy = SMACrossoverStrategy(strategy_config)
    signals = await strategy.run(
        session,
        account=account,
        instrument=instrument,
        emitted_at=clock.now(),
        correlation_id=correlation_id,
    )
    outcomes = await execute_signals(session, clock, broker, risk_config, signals)
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
```

Move the runtime imports above the existing top-of-file imports if ruff insists. If `Signal` is only used as a type, place it under `TYPE_CHECKING`; if used at runtime in the dataclass field annotation, keep it at module level.

- [ ] **Step 5: Run to confirm tests pass**

```
uv --directory engine run pytest tests/unit/test_pipeline.py -v
```

Expected: 9 passed (6 parser + 3 microtrader).

- [ ] **Step 6: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean. If ruff demands the imports be reorganised, apply `ruff check --fix` and re-run format check.

- [ ] **Step 7: Commit**

```
git add engine/src/snapd_invest/pipeline.py engine/tests/unit/test_pipeline.py
git commit -m "feat(pipeline): add run_microtrader_once

Extracts the orchestration that the existing POST /v1/run-once route
does today (strategy.run + execution.execute_signals) into a single
pipeline function so the scheduler can reuse the same code path.

Task: T-003"
```

---

## Task 4: Add `run_agent_once` to `pipeline.py`

**Files:**
- Modify: `engine/src/snapd_invest/pipeline.py`
- Modify: `engine/tests/unit/test_pipeline.py`

- [ ] **Step 1: Confirm signatures**

In `engine/src/snapd_invest/agent.py`, locate:
- `async def run_agent(session, clock, llm, *, agent, personality, watchlist, correlation_id) -> AgentRunResult`
- `AgentRunResult` has `agent_name`, `summary`, `signals` (list[Signal]).
- `async def ensure_default_agent(session, clock, *, account, personality=CONSERVATIVE_VALUE) -> AgentModel`.

In `engine/src/snapd_invest/recommendation.py`:
- `async def create_recommendation(session, clock, *, agent_id, signals, rationale, correlation_id) -> Recommendation`.

No code change in this step.

- [ ] **Step 2: Write the failing tests**

Append to `engine/tests/unit/test_pipeline.py`:

```python
from snapd_invest.agent import CONSERVATIVE_VALUE
from snapd_invest.llm import FakeLlmProvider
from snapd_invest.pipeline import run_agent_once


class TestRunAgentOnce:
    async def test_creates_recommendation_when_agent_emits_signal(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        llm = FakeLlmProvider()
        llm.enqueue_json(
            {
                "summary": "AAPL still undervalued.",
                "signals": [
                    {
                        "instrument_symbol": "AAPL",
                        "instrument_exchange": "NASDAQ",
                        "action": "buy",
                        "quantity": 5,
                        "conviction": 0.8,
                        "rationale": "below intrinsic value",
                    }
                ],
            }
        )

        outcome = await run_agent_once(
            db_session,
            fake_clock,
            llm,
            account=account,
            instrument=instrument,
            personality=CONSERVATIVE_VALUE,
        )

        assert outcome.recommendation_id is not None
        assert outcome.agent_name == CONSERVATIVE_VALUE.name
        assert "undervalued" in outcome.summary

    async def test_no_recommendation_when_agent_emits_nothing(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        llm = FakeLlmProvider()
        llm.enqueue_json(
            {
                "summary": "Skipping — too volatile.",
                "signals": [],
            }
        )

        outcome = await run_agent_once(
            db_session,
            fake_clock,
            llm,
            account=account,
            instrument=instrument,
            personality=CONSERVATIVE_VALUE,
        )

        assert outcome.recommendation_id is None
        assert "volatile" in outcome.summary
```

- [ ] **Step 3: Run to confirm failure**

```
uv --directory engine run pytest tests/unit/test_pipeline.py::TestRunAgentOnce -v
```

Expected: `ImportError: cannot import name 'run_agent_once'`.

- [ ] **Step 4: Implement `run_agent_once`**

Append to `engine/src/snapd_invest/pipeline.py`:

```python
from snapd_invest.agent import CONSERVATIVE_VALUE, Personality, ensure_default_agent, run_agent
from snapd_invest.recommendation import create_recommendation

# Re-declare at TYPE_CHECKING (existing if-block) the new types:
#   from snapd_invest.llm import ILlmProvider


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

    The function does NOT commit the session — the caller owns the
    transaction boundary.
    """
    agent = await ensure_default_agent(
        session, clock, account=account, personality=personality
    )
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
```

Add `from snapd_invest.llm import ILlmProvider` to the existing `if TYPE_CHECKING:` block in `pipeline.py`.

- [ ] **Step 5: Run to confirm tests pass**

```
uv --directory engine run pytest tests/unit/test_pipeline.py -v
```

Expected: 11 passed (parser 6 + microtrader 3 + agent 2).

- [ ] **Step 6: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean.

- [ ] **Step 7: Commit**

```
git add engine/src/snapd_invest/pipeline.py engine/tests/unit/test_pipeline.py
git commit -m "feat(pipeline): add run_agent_once

Extracts the orchestration that the existing POST /v1/agents/run
route does today (ensure_default_agent + run_agent +
create_recommendation) into a single pipeline function reusable from
the scheduled agent job.

Task: T-003"
```

---

## Task 5: Add `expire_overdue_recommendations` to `pipeline.py`

**Files:**
- Modify: `engine/src/snapd_invest/pipeline.py`
- Modify: `engine/tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/unit/test_pipeline.py`:

```python
from datetime import timedelta

from snapd_invest.pipeline import expire_overdue_recommendations
from snapd_invest.recommendation import create_recommendation
from snapd_invest.strategy import Signal


class TestExpireOverdueRecommendations:
    async def test_expires_overdue_leaves_fresh(
        self, db_session: AsyncSession, fake_clock: FakeClock
    ) -> None:
        account = await create_account(db_session, fake_clock, name="paper")
        instrument = await ensure_instrument(
            db_session,
            symbol="AAPL",
            exchange="NASDAQ",
            instrument_type="stock",
            currency="USD",
        )
        signal = Signal(
            source="test",
            account_id=account.id,
            instrument_symbol=instrument.symbol,
            instrument_exchange=instrument.exchange,
            action="buy",
            quantity=Decimal("1"),
            conviction=Decimal("0.8"),
            rationale="test",
            emitted_at=fake_clock.now(),
            correlation_id=None,
        )
        # Two recommendations: one with very short TTL, one with default.
        short = await create_recommendation(
            db_session,
            fake_clock,
            agent_id="agent-1",
            signals=[signal],
            rationale="short",
            ttl=timedelta(minutes=1),
        )
        fresh = await create_recommendation(
            db_session,
            fake_clock,
            agent_id="agent-1",
            signals=[signal],
            rationale="fresh",
        )

        # Advance the clock past the short TTL.
        fake_clock.advance(hours=2)

        expired_count = await expire_overdue_recommendations(db_session, fake_clock)

        assert expired_count == 1
        await db_session.refresh(short)
        await db_session.refresh(fresh)
        assert short.status == "expired"
        assert fresh.status == "pending"
```

- [ ] **Step 2: Run to confirm failure**

```
uv --directory engine run pytest tests/unit/test_pipeline.py::TestExpireOverdueRecommendations -v
```

Expected: `ImportError: cannot import name 'expire_overdue_recommendations'`.

- [ ] **Step 3: Implement the thin wrapper**

Append to `engine/src/snapd_invest/pipeline.py`:

```python
from snapd_invest.recommendation import expire_overdue


async def expire_overdue_recommendations(
    session: AsyncSession,
    clock: Clock,
) -> int:
    """Sweep pending recommendations whose deadline has passed and mark
    them expired. Returns the number of rows expired.

    Thin wrapper over `recommendation.expire_overdue`. Exists in the
    pipeline module so the scheduler imports a single coherent surface.
    """
    return await expire_overdue(session, clock)
```

- [ ] **Step 4: Run to confirm tests pass**

```
uv --directory engine run pytest tests/unit/test_pipeline.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean.

- [ ] **Step 6: Commit**

```
git add engine/src/snapd_invest/pipeline.py engine/tests/unit/test_pipeline.py
git commit -m "feat(pipeline): add expire_overdue_recommendations wrapper

Thin pipeline-level wrapper over recommendation.expire_overdue so
the scheduler imports its three tick-functions from one module.

Task: T-003"
```

---

## Task 6: Refactor `api.py` routes to delegate to pipeline

**Files:**
- Modify: `engine/src/snapd_invest/api.py`

This task is a behaviour-preserving refactor. Existing tests cover the routes; they must remain green.

- [ ] **Step 1: Run the suite to capture baseline**

```
uv --directory engine run pytest -q
```

Expected: 99 (existing) + 12 (new pipeline) = 111 tests passing. Capture the count.

- [ ] **Step 2: Refactor the `run_once` route**

Replace the body of `run_once` (lines roughly 311–368 in `engine/src/snapd_invest/api.py`) with a delegation:

```python
    @app.post("/v1/run-once", response_model=RunOnceResponseDto, tags=["microtrader"])
    async def run_once(
        request: Request,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        broker: Annotated[PaperBroker, Depends(broker_dep)],
        risk_config: Annotated[RiskConfig, Depends(risk_dep)],
        instrument_symbol: str = "AAPL",
        instrument_exchange: str = "NASDAQ",
    ) -> RunOnceResponseDto:
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        account = await get_account_by_name(session, "paper")
        if account is None:
            account = await create_account(
                session, clock, name="paper", initial_cash=Decimal("100000")
            )
        instrument = await ensure_instrument(
            session,
            symbol=instrument_symbol,
            exchange=instrument_exchange,
            instrument_type="stock",
            currency="USD",
        )
        outcome = await run_microtrader_once(
            session,
            clock,
            broker,
            risk_config,
            account=account,
            instrument=instrument,
            correlation_id=correlation_id,
        )
        return RunOnceResponseDto(
            correlation_id=correlation_id,
            strategy=SMACrossoverStrategy.name,  # type: ignore[attr-defined]
            signals=[
                SignalDto(
                    source=s.source,
                    instrument_symbol=s.instrument_symbol,
                    instrument_exchange=s.instrument_exchange,
                    action=s.action,
                    quantity=s.quantity,
                    conviction=s.conviction,
                    rationale=s.rationale,
                )
                for s in outcome.signals
            ],
            outcomes=outcome.execution_summaries,
        )
```

Note: `SMACrossoverStrategy.name` is a class attribute, accessible without instantiating. If mypy/ruff prefer, change to `"sma_crossover"` literal.

- [ ] **Step 3: Refactor the `run_agent_once` route**

Replace the body of the existing `@app.post("/v1/agents/run")` handler with:

```python
    @app.post("/v1/agents/run", tags=["agent"])
    async def run_agent_route(
        request: Request,
        session: Annotated[AsyncSession, Depends(session_dep)],
        clock: Annotated[Clock, Depends(clock_dep)],
        llm: Annotated[OllamaProvider, Depends(llm_dep)],
        instrument_symbol: str = "AAPL",
        instrument_exchange: str = "NASDAQ",
    ) -> dict[str, Any]:
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        account = await get_account_by_name(session, "paper")
        if account is None:
            account = await create_account(
                session, clock, name="paper", initial_cash=Decimal("100000")
            )
        instrument = await ensure_instrument(
            session,
            symbol=instrument_symbol,
            exchange=instrument_exchange,
            instrument_type="stock",
            currency="USD",
        )
        outcome = await run_agent_once(
            session,
            clock,
            llm,
            account=account,
            instrument=instrument,
            correlation_id=correlation_id,
        )
        return {
            "correlation_id": correlation_id,
            "agent": outcome.agent_name,
            "summary": outcome.summary,
            "recommendation_id": outcome.recommendation_id,
        }
```

Rename the function from `run_agent_once` to `run_agent_route` so it does not shadow the imported pipeline function.

- [ ] **Step 4: Update imports in `api.py`**

Add to the top-level imports:

```python
from snapd_invest.pipeline import run_agent_once, run_microtrader_once
```

Remove any now-unused imports (`run_agent`, `execute_signals`, `create_recommendation`, etc.) if they no longer appear elsewhere in `api.py`. Be conservative — only delete after grepping.

- [ ] **Step 5: Run the suite, confirm green**

```
uv --directory engine run pytest -q
```

Expected: 111 passed. If anything fails, the refactor changed behaviour — find the divergence.

- [ ] **Step 6: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean.

- [ ] **Step 7: Commit**

```
git add engine/src/snapd_invest/api.py
git commit -m "refactor(api): route handlers delegate to pipeline functions

POST /v1/run-once and POST /v1/agents/run now call
pipeline.run_microtrader_once / pipeline.run_agent_once. No
behaviour change — existing tests still pass — but the orchestration
code now lives in pipeline.py where the scheduler can reuse it.

Task: T-003"
```

---

## Task 7: Add `build_default_jobs` and error listener to `scheduler.py`

**Files:**
- Modify: `engine/src/snapd_invest/scheduler.py`
- Create: `engine/tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing factory tests**

Create `engine/tests/unit/test_scheduler.py`:

```python
"""Tests for `snapd_invest.scheduler`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from snapd_invest.broker import PaperBroker
from snapd_invest.config import Settings
from snapd_invest.llm import FakeLlmProvider
from snapd_invest.risk import RiskConfig
from snapd_invest.scheduler import JobConfig, build_default_jobs, build_scheduler

if TYPE_CHECKING:
    from snapd_invest.clock import FakeClock


class TestBuildDefaultJobs:
    def test_returns_three_jobs_with_settings_intervals(
        self, db_engine: object, fake_clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            microtrader_interval_minutes=2,
            agent_interval_minutes=10,
            recommendation_expire_interval_minutes=7,
        )
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker=PaperBroker(fake_clock),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )

        by_id = {job.job_id: job for job in jobs}
        assert set(by_id) == {"microtrader_tick", "agent_tick", "expire_overdue"}
        assert by_id["microtrader_tick"].minutes == 2
        assert by_id["agent_tick"].minutes == 10
        assert by_id["expire_overdue"].minutes == 7

    def test_job_ids_are_unique(
        self, db_engine: object, fake_clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker=PaperBroker(fake_clock),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=Settings(_env_file=None),  # type: ignore[call-arg]
        )
        ids = [j.job_id for j in jobs]
        assert len(ids) == len(set(ids))


class TestHandlerErrorIsolation:
    async def test_handler_exception_is_caught_and_logged(
        self, db_engine: object, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Each scheduler handler must swallow exceptions so APScheduler keeps
        the job armed for the next interval. We exercise this by configuring
        an invalid watchlist entry and asserting the handler completes
        normally (the failing parse is logged, not raised)."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            watchlist=["INVALID-NO-AT-SIGN"],
        )
        jobs = build_default_jobs(
            session_factory=factory,
            clock=fake_clock,
            broker=PaperBroker(fake_clock),
            llm=FakeLlmProvider(),
            risk_config=RiskConfig(),
            settings=settings,
        )
        microtrader_job = next(j for j in jobs if j.job_id == "microtrader_tick")

        # Direct invocation of the handler closure — no scheduler involved.
        await microtrader_job.handler()  # must NOT raise
```

- [ ] **Step 2: Run to confirm failure**

```
uv --directory engine run pytest tests/unit/test_scheduler.py -v
```

Expected: `ImportError: cannot import name 'build_default_jobs'`.

- [ ] **Step 3: Extend `scheduler.py`**

Replace `engine/src/snapd_invest/scheduler.py` with:

```python
"""Scheduled jobs.

Thin wrapper over APScheduler. Each registered job calls into pipeline.py
functions — the same code path used by the manual POST /v1/run-once and
POST /v1/agents/run routes.

The scheduler is started by the FastAPI lifespan in `api.py`. In tests,
construct `build_default_jobs(...)` directly and invoke handlers; the
integration test uses a real `AsyncIOScheduler` with a 1-second interval.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from snapd_invest.pipeline import (
    expire_overdue_recommendations,
    parse_watchlist_entry,
    run_agent_once,
    run_microtrader_once,
)
from snapd_invest.data import ensure_instrument
from snapd_invest.portfolio import create_account, get_account_by_name

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from snapd_invest.broker import IBroker
    from snapd_invest.clock import Clock
    from snapd_invest.config import Settings
    from snapd_invest.llm import ILlmProvider
    from snapd_invest.risk import RiskConfig

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

JobFn = Callable[[], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class JobConfig:
    job_id: str
    minutes: int
    handler: JobFn


def build_scheduler(jobs: list[JobConfig]) -> AsyncIOScheduler:
    """Create an `AsyncIOScheduler` with the provided jobs.

    Registers an `EVENT_JOB_ERROR` listener as a backstop — the per-handler
    try/except inside `build_default_jobs` is the primary defense.
    """
    scheduler = AsyncIOScheduler()
    for job in jobs:
        scheduler.add_job(
            job.handler,
            trigger=IntervalTrigger(minutes=job.minutes),
            id=job.job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.add_listener(_log_job_error, EVENT_JOB_ERROR)
    return scheduler


def _log_job_error(event: JobExecutionEvent) -> None:
    """APScheduler-level backstop. Logs any exception that slips past the
    per-handler try/except in `build_default_jobs`."""
    log.error(
        "scheduler_job_error_event",
        job_id=event.job_id,
        scheduled_run_time=event.scheduled_run_time.isoformat(),
        exception=str(event.exception) if event.exception else None,
    )


def build_default_jobs(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    broker: IBroker,
    llm: ILlmProvider,
    risk_config: RiskConfig,
    settings: Settings,
) -> list[JobConfig]:
    """Wire pipeline functions as APScheduler-friendly closures.

    Each handler opens its own session per tick, iterates the configured
    watchlist, and catches every exception so APScheduler keeps the job
    armed for the next interval.
    """

    async def _resolve_account_and_instrument(
        session: AsyncSession, entry: str
    ) -> tuple[object, object] | None:
        symbol, exchange = parse_watchlist_entry(entry)
        account = await get_account_by_name(session, settings.default_account_name)
        if account is None:
            log.warning(
                "scheduler_skipped",
                reason="account_missing",
                account=settings.default_account_name,
            )
            return None
        instrument = await ensure_instrument(
            session,
            symbol=symbol,
            exchange=exchange,
            instrument_type="stock",
            currency="USD",
        )
        return account, instrument

    async def _microtrader_handler() -> None:
        correlation_id = str(uuid.uuid4())
        for entry in settings.watchlist:
            try:
                async with session_factory() as session:
                    resolved = await _resolve_account_and_instrument(session, entry)
                    if resolved is None:
                        continue
                    account, instrument = resolved
                    await run_microtrader_once(
                        session,
                        clock,
                        broker,
                        risk_config,
                        account=account,
                        instrument=instrument,
                        correlation_id=correlation_id,
                    )
                    await session.commit()
            except Exception:
                log.exception(
                    "scheduler_job_failed", job="microtrader_tick", entry=entry
                )

    async def _agent_handler() -> None:
        correlation_id = str(uuid.uuid4())
        for entry in settings.watchlist:
            try:
                async with session_factory() as session:
                    resolved = await _resolve_account_and_instrument(session, entry)
                    if resolved is None:
                        continue
                    account, instrument = resolved
                    await run_agent_once(
                        session,
                        clock,
                        llm,
                        account=account,
                        instrument=instrument,
                        correlation_id=correlation_id,
                    )
                    await session.commit()
            except Exception:
                log.exception("scheduler_job_failed", job="agent_tick", entry=entry)

    async def _expire_handler() -> None:
        try:
            async with session_factory() as session:
                expired = await expire_overdue_recommendations(session, clock)
                await session.commit()
                if expired:
                    log.warning("recommendations_expired", count=expired)
                else:
                    log.info("recommendations_expired", count=0)
        except Exception:
            log.exception("scheduler_job_failed", job="expire_overdue")

    return [
        JobConfig(
            job_id="microtrader_tick",
            minutes=settings.microtrader_interval_minutes,
            handler=_microtrader_handler,
        ),
        JobConfig(
            job_id="agent_tick",
            minutes=settings.agent_interval_minutes,
            handler=_agent_handler,
        ),
        JobConfig(
            job_id="expire_overdue",
            minutes=settings.recommendation_expire_interval_minutes,
            handler=_expire_handler,
        ),
    ]
```

Note the `async with session_factory()` pattern returns a session that is closed at scope exit. We explicitly call `await session.commit()` inside the success path — the pipeline functions do not commit themselves.

The `_resolve_account_and_instrument` helper does NOT itself wrap exceptions; that's the outer per-handler try. If `parse_watchlist_entry` raises (e.g. for `INVALID-NO-AT-SIGN`), the outer `except` logs and continues to the next entry.

- [ ] **Step 4: Run to confirm tests pass**

```
uv --directory engine run pytest tests/unit/test_scheduler.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean. mypy may grumble about `tuple[object, object]` returns from `_resolve_account_and_instrument`; if so, tighten the return type to `tuple[Account, Instrument] | None` with the proper imports under `TYPE_CHECKING`.

- [ ] **Step 6: Commit**

```
git add engine/src/snapd_invest/scheduler.py engine/tests/unit/test_scheduler.py
git commit -m "feat(scheduler): add build_default_jobs and error listener

build_default_jobs wires the three pipeline functions as APScheduler
closures. Each closure opens its own session, iterates the watchlist,
and catches every exception so a broken tick never crashes the
scheduler. build_scheduler now also registers an EVENT_JOB_ERROR
listener as a backstop.

Task: T-003"
```

---

## Task 8: Scheduler integration test (real APScheduler, short interval)

**Files:**
- Modify: `engine/tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing integration test**

Append to `engine/tests/unit/test_scheduler.py`:

```python
from apscheduler.triggers.interval import IntervalTrigger


class TestSchedulerIntegration:
    async def test_scheduler_fires_handler(self) -> None:
        """Boot a real AsyncIOScheduler with a 1-second interval and verify
        the handler runs at least once before we shut the scheduler down."""
        counter = {"calls": 0}
        done = asyncio.Event()

        async def handler() -> None:
            counter["calls"] += 1
            done.set()

        scheduler = build_scheduler(
            [JobConfig(job_id="probe", minutes=0, handler=handler)]
        )
        # Override the trigger to a sub-minute interval — JobConfig.minutes
        # only accepts integers but the test needs faster turnaround.
        scheduler.reschedule_job("probe", trigger=IntervalTrigger(seconds=1))

        scheduler.start()
        try:
            await asyncio.wait_for(done.wait(), timeout=3.0)
        finally:
            scheduler.shutdown(wait=False)

        assert counter["calls"] >= 1

    async def test_scheduler_survives_handler_exception(self) -> None:
        """A handler that raises must not crash the scheduler — subsequent
        ticks still fire."""
        calls = 0
        done_after_failure = asyncio.Event()

        async def flaky_handler() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            done_after_failure.set()

        scheduler = build_scheduler(
            [JobConfig(job_id="flaky", minutes=0, handler=flaky_handler)]
        )
        scheduler.reschedule_job("flaky", trigger=IntervalTrigger(seconds=1))

        scheduler.start()
        try:
            await asyncio.wait_for(done_after_failure.wait(), timeout=4.0)
        finally:
            scheduler.shutdown(wait=False)

        assert calls >= 2
```

- [ ] **Step 2: Run to confirm tests pass**

(They should — `build_scheduler` already exists and the listener catches errors. If a test hangs near the 3- or 4-second timeout, increase the timeout once; if it consistently fails the assertion, investigate before increasing further.)

```
uv --directory engine run pytest tests/unit/test_scheduler.py::TestSchedulerIntegration -v
```

Expected: 2 passed in ~3-5 seconds.

- [ ] **Step 3: Verify the full test suite is still green**

```
uv --directory engine run pytest -q
```

Expected: ~116 passed (111 prior + 5 new = 3 factory + 2 integration). Actual count may differ by ±1 depending on previous tasks; just confirm 0 failures.

- [ ] **Step 4: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean. Note the integration test deliberately produces a few harmless "boom" log entries — that's the design.

- [ ] **Step 5: Commit**

```
git add engine/tests/unit/test_scheduler.py
git commit -m "test(scheduler): add integration test proving scheduler→handler wiring

Boots a real AsyncIOScheduler with a 1-second interval and verifies
the handler fires. A second test exercises the error-isolation
behavior: a handler that raises on the first call does not crash
the scheduler — the second tick still fires.

Task: T-003"
```

---

## Task 9: Wire the scheduler into the FastAPI lifespan

**Files:**
- Modify: `engine/src/snapd_invest/api.py`

- [ ] **Step 1: Confirm current lifespan shape**

Open `engine/src/snapd_invest/api.py` and locate the `lifespan` async context manager (around line 64-82). The current version sets up engine/session_factory/clock/broker/llm/risk_config but does not start the scheduler.

- [ ] **Step 2: Extend `lifespan`**

Replace the existing `lifespan` function with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    engine = make_engine(settings)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.clock = SystemClock()
    app.state.broker = PaperBroker(app.state.clock)
    app.state.llm = OllamaProvider()
    app.state.risk_config = RiskConfig()
    app.state.scheduler = None

    if settings.scheduler_enabled:
        jobs = build_default_jobs(
            session_factory=app.state.session_factory,
            clock=app.state.clock,
            broker=app.state.broker,
            llm=app.state.llm,
            risk_config=app.state.risk_config,
            settings=settings,
        )
        scheduler = build_scheduler(jobs)
        scheduler.start()
        app.state.scheduler = scheduler
        log.info("scheduler_started", job_count=len(jobs))
    else:
        log.info("scheduler_disabled")

    log.info("engine_started", version=__version__, db_path=str(settings.db_path))

    try:
        yield
    finally:
        if app.state.scheduler is not None:
            app.state.scheduler.shutdown(wait=False)
            log.info("scheduler_stopped")
        await app.state.llm.aclose()
        await engine.dispose()
        log.info("engine_stopped")
```

Add to the top-level imports of `api.py`:

```python
from snapd_invest.scheduler import build_default_jobs, build_scheduler
```

- [ ] **Step 3: Add a lifespan test that proves the scheduler is started/stopped on enable**

Append to `engine/tests/unit/test_api_health.py` (already exists — it tests app startup) — OR if there's a better-suited test file, use that. New tests:

```python
import asyncio

from httpx import ASGITransport, AsyncClient

from snapd_invest.api import create_app


async def test_scheduler_starts_and_stops_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When scheduler_enabled=True, the lifespan should start the scheduler
    and shut it down on exit."""
    monkeypatch.setenv("SNAPDINVEST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SNAPDINVEST_SCHEDULER_ENABLED", "true")
    # Disable the real OllamaProvider by setting an unused URL so aclose() works.
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:1")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Trigger lifespan startup
        resp = await client.get("/v1/health")
        assert resp.status_code == 200
        assert app.state.scheduler is not None
        assert app.state.scheduler.running is True

    # On exit, lifespan ran the shutdown — scheduler should be stopped.
    assert app.state.scheduler.running is False


async def test_scheduler_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SNAPDINVEST_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SNAPDINVEST_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:1")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/health")
        assert resp.status_code == 200
        assert app.state.scheduler is None
```

If `test_api_health.py` doesn't import `pytest`, `Path`, `AsyncClient`, `ASGITransport`, or `asyncio`, add them at the top.

Database migrations may also be required for the in-memory app to function — confirm by running the test. If migrations need to be applied to the test's SQLite file, either: (a) call `Base.metadata.create_all` inside the test fixture, or (b) set `SNAPDINVEST_DB_PATH` to `:memory:` and ensure the app initializes the schema in `lifespan`. The existing `test_api_health.py` already handles this — mirror its pattern.

- [ ] **Step 4: Run the new lifespan tests**

```
uv --directory engine run pytest tests/unit/test_api_health.py -v
```

Expected: existing tests pass + 2 new pass. If the in-memory database setup is awkward, fall back to a tmp_path-backed SQLite file (`SNAPDINVEST_DB_PATH=...`).

- [ ] **Step 5: Run the full suite**

```
uv --directory engine run pytest -q
```

Expected: 0 failures.

- [ ] **Step 6: Lint + type + format**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
```

Expected: clean.

- [ ] **Step 7: Commit**

```
git add engine/src/snapd_invest/api.py engine/tests/unit/test_api_health.py
git commit -m "feat(api): start/stop scheduler in FastAPI lifespan

The lifespan now calls build_default_jobs + build_scheduler when
Settings.scheduler_enabled is true, attaches the scheduler to
app.state.scheduler, and shuts it down cleanly on app exit. When the
flag is false (tests, dev), the scheduler is not started.

Task: T-003"
```

---

## Task 10: Update docs and `.env.example`

**Files:**
- Modify: `engine/.env.example`
- Modify: `docs/architecture/module-map.md`

- [ ] **Step 1: Add new env vars to `.env.example`**

Append to `engine/.env.example`:

```
# Scheduler
SNAPDINVEST_SCHEDULER_ENABLED=true
SNAPDINVEST_MICROTRADER_INTERVAL_MINUTES=1
SNAPDINVEST_AGENT_INTERVAL_MINUTES=30
SNAPDINVEST_RECOMMENDATION_EXPIRE_INTERVAL_MINUTES=5

# Watchlist (comma-separated SYMBOL@EXCHANGE entries)
SNAPDINVEST_WATCHLIST=AAPL@NASDAQ
SNAPDINVEST_DEFAULT_ACCOUNT_NAME=paper
```

- [ ] **Step 2: Add `pipeline.py` row to the module map**

In `docs/architecture/module-map.md`, find the Python engine modules table and add a row between `recommendation.py` and `execution.py` (or in alphabetical order if the existing rows are alphabetised):

```
| `pipeline.py` | Per-tick orchestration: `run_microtrader_once`, `run_agent_once`, `expire_overdue_recommendations`, `parse_watchlist_entry` | `strategy`, `execution`, `agent`, `recommendation` |
```

If the existing table layout differs, follow the existing pattern verbatim.

- [ ] **Step 3: Commit**

```
git add engine/.env.example docs/architecture/module-map.md
git commit -m "docs(T-003): document scheduler env vars and pipeline module

Adds the new SNAPDINVEST_* env vars to .env.example so a fresh
clone documents the scheduler knobs out of the box, and adds the
pipeline.py row to the module map.

Task: T-003"
```

---

## Task 11: Final verification + PR

- [ ] **Step 1: Run the full suite end-to-end from a fresh state**

```
uv --directory engine run ruff check
uv --directory engine run ruff format --check
uv --directory engine run mypy src
uv --directory engine run pytest -v
```

Expected: every check clean, ~116 tests passing.

- [ ] **Step 2: Manual sanity-check the engine boots**

Launch the engine with the scheduler enabled and verify the start-up log:

```
uv --directory engine run uvicorn snapd_invest.api:app --port 8000
```

In the logs, confirm:
- `scheduler_started` event with `job_count=3`
- `engine_started`
- After Ctrl-C: `scheduler_stopped` then `engine_stopped`

Stop the server.

- [ ] **Step 3: Push and open the PR**

The user supplies a PAT with `repo` + `workflow` scopes (see memory `github-credentials.md`). Use it as:

```
git push https://x-access-token:<PAT>@github.com/torbenboeck/snapd-invest.git HEAD:refs/heads/feature/T-003-scheduler-wiring
```

Open the PR via `gh`:

```
GH_TOKEN=<PAT> gh pr create --repo torbenboeck/snapd-invest --base main --head feature/T-003-scheduler-wiring --title "feat: T-003 scheduler wiring — autonomous MicroTrader, agent, and expire jobs" --body "<see commit messages + spec link>"
```

- [ ] **Step 4: Update `tasks/_next.md`**

Edit `tasks/_next.md` so the `Next:` pointer moves to `T-002-yfinance-real-data.md` (T-002 is the most natural successor — it unlocks real market data for the scheduler MicroTrader job). Update the backlog list to reflect T-003 as done.

```
git add tasks/_next.md
git commit -m "chore(tasks): mark T-003 done; T-002 is next

Task: T-003"
```

(This commit can land in the PR or as a follow-up depending on whether CI runs require it before merge.)

- [ ] **Step 5: Mark T-003 done in its task file**

In `tasks/T-003-scheduler-wiring.md`, change `**Status:** pending` to `**Status:** done` and tick the acceptance-criteria checkboxes. Include this in the PR.

---

## Done definition

T-003 is complete when:

- All 116+ tests pass in CI on the PR.
- `make lint` and `make test` are clean.
- A reviewer can read the spec, then the PR diff, and understand exactly what was built and why.
- The engine boots, logs `scheduler_started`, and stops cleanly.
- `tasks/_next.md` points to T-002.
