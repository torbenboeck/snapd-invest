# Next task pointer

The next available task is tracked here so agents picking up work autonomously
can find it without scanning the directory.

**Next:** `T-001-saxo-sim-integration.md`

When a task is completed:

1. Update the task file's status to `done`.
2. Update this pointer to the next pending task ID, or `(none)` if the queue is empty.
3. Commit both changes together.

## Current backlog (priority order)

1. T-001 — Saxo SIM integration
2. T-002 — Real market data via yfinance
3. T-003 — Wire the APScheduler into FastAPI lifespan
4. T-004 — End-to-end pipeline test
5. T-005 — Generate the .NET client from OpenAPI via NSwag
