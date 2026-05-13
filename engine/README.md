# engine

Python service: trading core, agents, broker adapters, persistence.

See [`CLAUDE.md`](CLAUDE.md) for full guidance.

## Quick reference

```bash
# Install
uv sync

# Run (development)
uv run uvicorn algo_invest.api:app --reload --port 8000

# Apply DB migrations
uv run alembic upgrade head

# Test
uv run pytest

# Lint
uv run ruff check
uv run ruff format --check
uv run mypy src

# Format
uv run ruff format
uv run ruff check --fix
```

## Module layout

```
src/algo_invest/
├── api.py              FastAPI app, routes, DI
├── audit.py            Event log (append-only)
├── clock.py            Injectable clock
├── config.py           Settings from env
├── logging_config.py   structlog setup
├── models.py           SQLAlchemy ORM models
└── persistence.py      Engine + session factory

tests/
├── conftest.py         Shared fixtures (in-memory DB, FakeClock)
└── unit/               One test file per module
```

More modules land per PR. Each gets its own one-pager in this README's appendix when stable.
