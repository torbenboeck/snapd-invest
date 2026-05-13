# T-003 — Scheduler wiring

**Status:** Design accepted, ready for implementation plan
**Created:** 2026-05-13
**Owner:** Claude Code
**Task:** [`tasks/T-003-scheduler-wiring.md`](../../tasks/T-003-scheduler-wiring.md)

---

## Goal

Wire `APScheduler` into FastAPI's `lifespan` so MicroTrader, the agent, and
recommendation-expiry run autonomously inside the engine process. Manual
`POST /v1/run-once` and the scheduled tick must go through **the same**
business-logic function, so there is one code path to maintain and reason
about.

## Non-goals (deferred to other tasks)

- **Market-data refresh job** — deferred to T-002 (yfinance). The scheduler
  has no data-refresh job in T-003. The MicroTrader's existing dependency
  on bars in DB is unchanged; with no refresh job running, the scheduled
  tick simply emits no signal until data is present, which is correct
  behavior.
- **Cron-based triggers** (e.g. only during market hours) — interval-only.
- **Per-instrument intervals** — all watchlist members share the same
  tick frequency.
- **Distributed scheduling** (Celery, Redis-backed) — out of scope for the
  single-user MVP.
- **Per-personality agent config** — only the MVP `CONSERVATIVE_VALUE`
  personality is scheduled.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  FastAPI app (api.py)                                        │
│                                                              │
│  lifespan() ─┬─► engine, session_factory, clock, broker, …   │
│              └─► scheduler = build_scheduler(jobs)           │
│                  scheduler.start()  ◄── only if enabled      │
│                                                              │
│  routes:                                                     │
│   POST /v1/run-once ──────► pipeline.run_microtrader_once    │
│   POST /v1/agents/run ────► pipeline.run_agent_once          │
└──────────────────────────────────────────────────────────────┘
                                  │ (same functions, no HTTP)
                                  ▼
┌──────────────────────────────────────────────────────────────┐
│  pipeline.py (NEW)                                           │
│                                                              │
│  Orchestration. Pure-ish: takes session + dependencies,      │
│  runs strategy.run + execution.execute_signals (or the       │
│  agent equivalent), returns the outcome. No HTTP, no         │
│  APScheduler.                                                │
└──────────────────────────────────────────────────────────────┘
                                  ▲
                                  │
┌──────────────────────────────────────────────────────────────┐
│  scheduler.py (extended)                                     │
│                                                              │
│  build_scheduler(jobs)               ◄── existing            │
│  build_default_jobs(...)             ◄── NEW                 │
│      Wraps pipeline functions in scheduler-friendly          │
│      closures: opens its own session per tick, catches       │
│      and logs every exception, never re-raises.              │
└──────────────────────────────────────────────────────────────┘
```

### Key idea

`pipeline.py` owns "what happens per tick". HTTP routes and scheduled jobs
are two ways to trigger the same pipeline functions. The scheduler does not
know about HTTP; the routes do not know about APScheduler.

## Components

### `config.py` — Settings additions

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Scheduler
    scheduler_enabled: bool = Field(default=True)
    microtrader_interval_minutes: int = Field(default=1, ge=1)
    agent_interval_minutes: int = Field(default=30, ge=1)
    recommendation_expire_interval_minutes: int = Field(default=5, ge=1)

    # Watchlist — comma-separated SYMBOL@EXCHANGE strings
    watchlist: list[str] = Field(
        default_factory=lambda: ["AAPL@NASDAQ"],
    )
    default_account_name: str = Field(default="paper")
```

Env override examples:

- `SNAPDINVEST_SCHEDULER_ENABLED=false`
- `SNAPDINVEST_WATCHLIST=AAPL@NASDAQ,BTC-USD@BINANCE`
- `SNAPDINVEST_MICROTRADER_INTERVAL_MINUTES=5`

Pydantic-settings parses `list[str]` from comma-separated env values
automatically. A small helper in `pipeline.py` splits each entry into
`(symbol, exchange)`; invalid entries raise `ValueError` at startup, not at
tick time.

### `pipeline.py` (new) — orchestration functions

```python
# pipeline.py

@dataclass(slots=True, frozen=True)
class MicroTraderOutcome:
    """Result of one MicroTrader tick for one instrument."""
    instrument: Instrument
    signals: list[Signal]
    execution_summaries: list[dict[str, Any]]


@dataclass(slots=True, frozen=True)
class AgentOutcome:
    """Result of one agent run for one instrument."""
    instrument: Instrument
    recommendation_id: str | None
    summary: str


async def run_microtrader_once(
    session: AsyncSession,
    clock: Clock,
    broker: IBroker,
    risk_config: RiskConfig,
    *,
    account: Account,
    instrument: Instrument,
    correlation_id: str | None = None,
) -> MicroTraderOutcome:
    """One MicroTrader tick: build strategy, emit signals, execute through
    the risk gate. Called by POST /v1/run-once and by the scheduled job."""
    ...


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
    """One agent tick: run agent, package signals as a Recommendation."""
    ...


def parse_watchlist_entry(entry: str) -> tuple[str, str]:
    """Parse 'SYMBOL@EXCHANGE' into (symbol, exchange). Raises ValueError
    with a clear message on malformed input. Sync — no I/O."""
    ...
```

### `scheduler.py` — extended

```python
# scheduler.py — existing build_scheduler stays unchanged

def build_default_jobs(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    broker: IBroker,
    llm: ILlmProvider,
    risk_config: RiskConfig,
    settings: Settings,
) -> list[JobConfig]:
    """Wire pipeline functions as APScheduler jobs.

    Each handler is a closure that:
      1. Opens a new AsyncSession per invocation.
      2. Iterates the configured watchlist.
      3. Catches every exception, logs it with structlog, never re-raises.
    """
    ...
```

The handlers run with a fresh session per tick (per the engine/CLAUDE.md
rule "One AsyncSession per request" — same here, one per scheduled tick).

### `api.py` — lifespan integration

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    clock = SystemClock()
    broker = PaperBroker(clock)
    llm = OllamaProvider()
    risk_config = RiskConfig()

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.clock = clock
    app.state.broker = broker
    app.state.llm = llm
    app.state.risk_config = risk_config
    app.state.scheduler = None  # set below if enabled

    if settings.scheduler_enabled:
        jobs = build_default_jobs(
            session_factory=session_factory,
            clock=clock,
            broker=broker,
            llm=llm,
            risk_config=risk_config,
            settings=settings,
        )
        scheduler = build_scheduler(jobs)
        scheduler.start()
        app.state.scheduler = scheduler
        log.info("scheduler_started", job_count=len(jobs))

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

The existing `POST /v1/run-once` and `POST /v1/agents/run` handlers are
refactored to delegate to `pipeline.run_microtrader_once` /
`pipeline.run_agent_once`. DTO building stays in the route; orchestration
moves to pipeline.

## Data flow — one scheduled MicroTrader tick

1. APScheduler fires `microtrader_tick` closure.
2. Closure opens `AsyncSession` from `session_factory`.
3. Closure generates a fresh `correlation_id` (uuid4) for traceability.
4. Closure iterates `settings.watchlist`:
   a. `parse_watchlist_entry("AAPL@NASDAQ") → ("AAPL", "NASDAQ")`
   b. `ensure_instrument(session, symbol="AAPL", exchange="NASDAQ", …)`
   c. `account = await get_account_by_name(session, settings.default_account_name)`
   d. `await pipeline.run_microtrader_once(session, clock, broker,
      risk_config, account=account, instrument=instrument,
      correlation_id=…)`
5. Closure commits the session (success) OR rolls back (exception).
6. Any exception is caught, logged with `structlog.exception(...)`, never
   re-raised. APScheduler keeps the job armed for the next interval.
7. Session is closed.

The agent and expire-overdue jobs follow the same shape: one session per
tick, watchlist loop, per-instrument call, commit-or-rollback, exception
caught at the boundary.

## Error handling

Three layers:

1. **Per-tick try/except inside each closure** — the primary defense. A
   broken pipeline call must never crash the scheduler thread. The closure
   logs with `structlog.exception("scheduler_job_failed", job=job_id, …)`
   and returns normally so APScheduler proceeds to the next interval.
2. **APScheduler `EVENT_JOB_ERROR` listener** — registered as a backstop in
   `build_scheduler`. Fires only if the per-tick try/except misses
   something. Logs with the same structure for grep-ability.
3. **Lifespan cleanup** — if scheduler fails to start, lifespan re-raises
   so the engine refuses to come up rather than running half-initialized.

Recommendation-expire job logs at `info` level when nothing expires (it
will be the common case), `warning` when it expires recommendations.
MicroTrader/agent jobs log at `info` per tick for traceability.

### Missing-prerequisite handling

If `get_account_by_name(settings.default_account_name)` returns `None` at
tick time (account row not yet created), the scheduled job logs a
`warning` ("scheduler_skipped: account_missing") and returns. The next
tick retries. This keeps a fresh installation usable — the user creates
the paper account once via the CLI (or via the bootstrap path), then the
scheduler catches up.

Same shape if `ensure_instrument` fails for some watchlist entry — log
warning, skip just that entry, continue with the rest.

## Testing

Two layers, both required by the task acceptance criteria:

### Unit tests — `tests/unit/test_pipeline.py` (new)

Test the pipeline functions directly with the existing test fixtures
(`db_session`, `fake_clock`):

- `test_run_microtrader_once_emits_signal_on_golden_cross` — seed bars,
  call function, assert `outcome.signals` contains a buy.
- `test_run_microtrader_once_no_signal_when_no_bars` — empty DB, call,
  assert empty signals and no orders created.
- `test_run_microtrader_once_risk_rejects` — seed bars + low cash, call,
  assert order count is 0 and audit shows `risk_decision: rejected`.
- `test_run_agent_once_creates_recommendation` — seed bars + `FakeLlmProvider`
  with canned response, call, assert recommendation persisted with `pending`
  status.
- `test_run_agent_once_no_recommendation_when_no_signals` — same setup,
  FakeLlm returns low-conviction signals, assert no recommendation row.
- `test_parse_watchlist_entry_valid` / `_invalid` — happy + error paths.

These run synchronously, sub-second, no scheduler involved.

### Integration test — `tests/unit/test_scheduler.py` (new)

One test that boots a real `AsyncIOScheduler` to prove the wiring works:

- `test_scheduler_fires_job_handler` — register one job with
  `IntervalTrigger(seconds=1)` whose handler increments an `asyncio.Event`-
  backed counter. `scheduler.start()`, `await asyncio.sleep(1.5)`,
  `scheduler.shutdown()`, assert counter ≥ 1.
- `test_scheduler_handler_exception_does_not_crash` — handler raises;
  scheduler should still be running after sleep, second tick still fires.
- `test_build_default_jobs_uses_settings_intervals` — call factory with
  Settings overrides, assert JobConfig minutes match.

Avoids real APScheduler timing flakiness by keeping the integration test
scope narrow: one job, short interval, single assertion. The pipeline
behavior is covered by the unit tests above; the integration test only
proves "the closures actually run inside APScheduler".

### Out of scope for T-003 tests

- End-to-end through the FastAPI surface — that is T-004.
- Real-time clock dependencies (we use `IntervalTrigger`, which is
  monotonic-time based, not wall clock).

## Files in scope

| File | Change |
|---|---|
| `engine/src/snapd_invest/config.py` | Add scheduler/watchlist Settings fields |
| `engine/src/snapd_invest/pipeline.py` | **NEW** — orchestration functions |
| `engine/src/snapd_invest/scheduler.py` | Add `build_default_jobs`, error listener |
| `engine/src/snapd_invest/api.py` | Wire scheduler into lifespan; refactor existing routes to call pipeline functions |
| `engine/tests/unit/test_pipeline.py` | **NEW** — pipeline unit tests |
| `engine/tests/unit/test_scheduler.py` | **NEW** — scheduler integration test |
| `engine/.env.example` | Add new env vars |
| `docs/architecture/module-map.md` | Add `pipeline.py` to module table |

`tasks/T-003-scheduler-wiring.md` listed `api.py`, `scheduler.py`, `config.py`,
`tests/unit/test_scheduler.py` as files in scope. This spec extends that
with `pipeline.py` and `test_pipeline.py` based on the design decision
made during brainstorming.

## Acceptance criteria (from task file, restated)

- [x] FastAPI lifespan starts and stops scheduler cleanly — covered by
      api.py changes and lifespan teardown logic.
- [x] Job intervals configurable via Settings — covered by config.py
      additions.
- [x] Jobs use `coalesce=True` and `max_instances=1` — already in
      `build_scheduler`, unchanged.
- [x] Manual `POST /v1/run-once` and scheduled tick share the same handler
      — both call `pipeline.run_microtrader_once`.
- [x] Errors inside a job are logged, never crash the scheduler — three-
      layer error handling described above.
- [x] When `Settings.scheduler_enabled=False`, scheduler is NOT started —
      lifespan branches on the flag.
- [x] Integration test using `FakeMarketDataProvider` proves a scheduled
      job runs end-to-end — **partial deviation from task wording.** The
      task file mentions `FakeMarketDataProvider`, which belongs to T-002.
      In T-003 the integration test asserts scheduler→handler wiring
      (`test_scheduler_fires_job_handler`); the data-flow side is covered
      by pipeline unit tests with pre-seeded bars. This was chosen
      deliberately during brainstorming (option "Begge: pipeline-unit +
      scheduler-integration") to avoid timing flakiness; when T-002 lands,
      a wider end-to-end test (T-004) will exercise the data-flow chain.

## Implementation order

Suggested for the plan that follows:

1. `config.py` — add Settings fields + tests.
2. `pipeline.py` — extract `run_microtrader_once`, `run_agent_once`,
   `expire_overdue_recommendations` from `api.py`/`recommendation.py`.
3. `tests/unit/test_pipeline.py` — unit tests against the new functions.
4. Refactor `api.py` routes to call pipeline functions (no behavior
   change at this point — just a move).
5. `scheduler.py` — add `build_default_jobs` and error listener.
6. `tests/unit/test_scheduler.py` — integration test.
7. `api.py` lifespan — wire scheduler start/stop.
8. `module-map.md` + `.env.example` — docs.

Each step keeps the suite green; failures are localized to the step that
introduced them.
