# Next task pointer

The next available task is tracked here so agents picking up work autonomously
can find it without scanning the directory.

**Next:** `T-001-B-saxo-trading.md`

When a task is completed:

1. Update the task file's status to `done`.
2. Update this pointer to the next pending task ID, or `(none)` if the queue is empty.
3. Commit both changes together.

## Current backlog (priority order)

1. T-001-B — Saxo SIM trading: `place_order`, `get_positions`, `get_last_price`, idempotency, MicroTrader wiring  *(needs Saxo SIM dev-app credentials from user for the optional live SIM placement test; unit tests run without)*
2. T-002 — Real market data via yfinance
3. T-004 — End-to-end pipeline test
4. T-005 — Generate the .NET client from OpenAPI via NSwag

## Completed

- T-001-A — Saxo SIM OAuth + `get_account` *(2026-05-14, PR #5)*
- T-003 — Wire the APScheduler into FastAPI lifespan *(2026-05-14, PR #2)*

## Archived

- T-001 — Saxo SIM integration *(superseded — split into T-001-A done + T-001-B pending; archived in `.archive/`)*
