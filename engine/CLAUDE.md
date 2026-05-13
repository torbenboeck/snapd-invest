# engine — Python trading service

Python 3.12+ service exposing a FastAPI HTTP interface. Owns trading logic, agents, broker adapters, and persistence.

Personal preferences live in `~/.claude/CLAUDE.md`. Repo-level guidance is in `../CLAUDE.md` (and `../AGENTS.md` for the operating manual).

---

## Stack

- **Python 3.12+**
- **uv** for package management and virtual env. Not pip, not poetry.
- **FastAPI** for HTTP. Pydantic V2 for request/response models and validation.
- **SQLAlchemy 2.x** (async) for ORM. **Alembic** for migrations.
- **APScheduler** for scheduled jobs.
- **structlog** for structured logging.
- **httpx** for outbound HTTP (LLM, brokers, market data).
- **yfinance** + **ccxt** for free market data (stocks/ETFs + crypto).
- **pytest** + **pytest-asyncio** + **respx** (httpx mocks) for tests.
- **ruff** for lint + format. **mypy** for type check.

No pandas at MVP — only used if a strategy explicitly needs it. Use raw lists + numpy where possible.

---

## Module layout (flat, intentional)

One file per concept. Split when a file grows past ~300 lines or develops sub-concerns.

```
src/algo_invest/
├── __init__.py
├── api.py              FastAPI app, routes, DI
├── audit.py            AuditEvent recording + queries
├── agent.py            LLM-powered agents
├── broker.py           IBroker protocol + PaperBroker + (later) SaxoBroker
├── clock.py            Clock protocol + SystemClock + FakeClock
├── data.py             Market data fetching (yfinance, ccxt) + caching
├── execution.py        Signal → Order pipeline; recommendation execution
├── indicators.py       Pure math: SMA, EMA, RSI, ATR, ...
├── llm.py              ILlmProvider protocol + OllamaProvider + FakeLlmProvider
├── models.py           SQLAlchemy ORM models (single source of truth)
├── persistence.py      Engine, session factory, base
├── portfolio.py        Positions, cash, P&L
├── recommendation.py   Recommendation queue + approval lifecycle
├── risk.py             Risk gate
├── scheduler.py        APScheduler jobs
└── strategy.py         Deterministic strategies (SMA crossover, ...)
```

Tests mirror this:

```
tests/
├── unit/               One test file per module
└── e2e/                Feature-level tests through the API
```

---

## Conventions

### Types

- **Type hints everywhere.** mypy in strict mode (`disallow_untyped_defs`, `disallow_any_generics`, etc.).
- **Pydantic V2** for HTTP request/response and structured LLM output.
- **dataclasses** (`@dataclass(slots=True, frozen=True)`) for internal value types.
- **`Protocol`** for abstractions (`IBroker`, `Clock`, `ILlmProvider`). Not ABCs.

### Time

- **Never call `datetime.utcnow()` or `datetime.now()` in production code.**
- Inject the `Clock` protocol; use `clock.now()`.
- Tests use `FakeClock` for deterministic time.

### Money and quantity

- **Use `decimal.Decimal`**, never `float`, for prices and quantities.
- Pydantic models declare `Decimal` directly; SQLAlchemy maps to `Numeric(18, 4)`.

### Errors

- **Exceptions for exceptional cases** (programming bugs, infrastructure failure, unexpected states).
- **Result types for expected business outcomes.** Use `returns.Result` *or* return a discriminated `Pydantic` union — pick one and be consistent. (Decision pending — see below.)
- Never swallow exceptions silently. Always log with context.

### Logging

- **`structlog`** with JSON output in non-dev environments.
- **Never use `print()`** in production code.
- Bind correlation_id to every request scope (FastAPI middleware).
- Don't log secrets, full request bodies on payment endpoints, or extracted PII.

```python
# Good
log.info("signal_emitted", strategy=name, instrument=symbol, conviction=conviction)

# Bad
print(f"Signal: {symbol}")
log.info(f"Signal emitted for {symbol}")  # f-string hides structure
```

### Async

- **Async everywhere** for I/O. Use `asyncio` natively.
- **`httpx.AsyncClient`** for HTTP. Reuse the client (don't create per call).
- **No blocking calls in async functions.** Use `asyncio.to_thread()` if you must call sync code.

### Database

- **One SQLAlchemy `AsyncSession` per request**, injected via FastAPI dependency.
- **No raw SQL** unless wrapped in `text()` and explicitly documented.
- **Avoid SQLite-only features** (Alembic must work against PostgreSQL later).
- **Decimal precision:** declare `Numeric(precision=18, scale=4)` for monetary values.
- **UTC timestamps:** use `DateTime(timezone=True)`, store UTC.

### IDs

- **Generate IDs at the boundary** (API layer or factory), not inside business logic. Inject a `make_id()` function for testability.
- Use `uuid.UUID` for primary keys. Encode as string in API responses.

### Naming

- **`snake_case`** for functions, variables, modules.
- **`PascalCase`** for classes.
- **`SCREAMING_SNAKE_CASE`** for constants.
- **Business-language nouns** for entities (`Order`, `Position`, `Trade`) — same terms as `../docs/ubiquitous-language.md`.
- **Imperative verbs** for actions (`submit_order`, `approve_recommendation`). Avoid CRUD verbs in domain code.

---

## Forbidden patterns

- **No business logic in `api.py`.** Routes are HTTP wiring. Logic goes in `strategy.py`, `agent.py`, `execution.py`, etc.
- **No direct DB access in route handlers.** Always go through a service function.
- **No live LLM calls in tests.** Use `FakeLlmProvider`.
- **No live broker calls in tests.** Use `PaperBroker` or `FakeBroker`.
- **No `time.sleep` in production code.** Use async/scheduler.
- **No global mutable state.** Use dependency injection.
- **No `# type: ignore`** without an inline comment explaining why.
- **No new dependency** without it being justified in `../docs/architecture/decision-log.md` first.

---

## Testing

- **xUnit-equivalent: pytest.** Naming: `test_<unit>_<scenario>_<expected>` (e.g. `test_sma_golden_cross_emits_buy_signal`).
- **One test file per module:** `strategy.py` → `tests/unit/test_strategy.py`.
- **Hand-written fakes** for repeatedly used dependencies (`FakeBroker`, `FakeClock`, `FakeLlmProvider`). Shared in `tests/_fakes/`.
- **`pytest-asyncio`** with `asyncio_mode = "auto"`.
- **E2E tests** use `httpx.AsyncClient(app=app)` against the FastAPI app — no real network.
- **Coverage target:** ≥ 80% on `strategy`, `risk`, `broker`, `portfolio`, `recommendation`. Other modules: best effort.

---

## Commands

```bash
# Install
uv sync

# Run
uv run uvicorn algo_invest.api:app --reload --port 8000

# Test
uv run pytest
uv run pytest tests/unit
uv run pytest -k sma_crossover

# Lint + format
uv run ruff check
uv run ruff format
uv run mypy src

# Database
uv run alembic upgrade head           # apply migrations
uv run alembic revision --autogenerate -m "<message>"  # create migration
uv run alembic downgrade -1           # rollback one (ask first via .claude hooks)
```

---

## Open questions specific to engine

- **Result type:** `returns.Result` vs. typed discriminated unions vs. exceptions for business outcomes? Decision deferred until first non-trivial business operation lands.
- **Ollama model choice:** llama3.1, mistral, qwen — pick based on JSON output reliability for agent recommendations.
- **Market data caching strategy:** SQLite table vs. parquet files vs. in-memory only? Decide when latency becomes a concern.
