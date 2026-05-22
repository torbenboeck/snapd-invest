# Next task pointer

The next available task is tracked here so agents picking up work autonomously
can find it without scanning the directory.

**Next:** `T-002-yfinance-real-data.md`

When a task is completed:

1. Update the task file's status to `done`.
2. Update this pointer to the next pending task ID, or `(none)` if the queue is empty.
3. Commit both changes together.

## Current backlog (priority order)

1. T-002 — Real market data via yfinance (paper accounts)
2. T-004 — End-to-end pipeline test
3. T-005 — Generate the .NET client from OpenAPI via NSwag

## Completed

- T-003 — Wire the APScheduler into FastAPI lifespan *(2026-05-14, PR #2)*
- T-001-A — Saxo SIM OAuth + `get_account` *(2026-05-15, PR #5)*
- T-001-B — Saxo SIM trading (place, cancel, positions, manual CLI) *(2026-05-21)*
- T-006 — Saxo bar data via `/chart/v1/charts` *(2026-05-22)*
- T-007 — Scheduler SIM-aware (autonomous MicroTrader on Saxo SIM) *(2026-05-22)*

## Archived

- T-001 — Saxo SIM integration *(superseded — split into T-001-A done + T-001-B pending; archived in `.archive/`)*
