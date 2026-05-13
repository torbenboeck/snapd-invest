# T-003 — Wire the APScheduler into FastAPI lifespan

**Status:** pending
**Created:** 2026-05-12
**Owner:** Claude Code
**Blocked by:** T-002

## Context

`scheduler.py` defines the scheduler factory but the FastAPI app doesn't start it. We need:

- MicroTrader tick every minute (configurable)
- Agent run every 30 minutes (configurable)
- Market-data refresh every N seconds (configurable; default 60s)
- Recommendation `expire_overdue` every 5 minutes

All jobs must use the same handler functions exposed by the HTTP API, so manual `/run-once` and scheduled execution share one code path.

## Acceptance criteria

- [ ] FastAPI `lifespan` starts and stops the scheduler cleanly
- [ ] Job intervals configurable via `Settings` (minutes for each job)
- [ ] Jobs use `coalesce=True` and `max_instances=1` (already set in `build_scheduler`)
- [ ] Manual `POST /v1/run-once` and scheduled tick share the same handler
- [ ] Errors inside a job are logged, never crash the scheduler
- [ ] When `Settings.scheduler_enabled=False`, the scheduler is NOT started (useful for tests and dev)
- [ ] Integration test using `FakeMarketDataProvider` proves a scheduled job runs end-to-end

## Files in scope

- `engine/src/algo_invest/scheduler.py`
- `engine/src/algo_invest/api.py`
- `engine/src/algo_invest/config.py`
- `engine/tests/unit/test_scheduler.py` (new)

## Out of scope

- Cron-based triggers (e.g. only during market hours)
- Distributed scheduling (Redis-backed, Celery, etc.)

## Verify

```bash
cd engine
uv run ruff check
uv run mypy src
uv run pytest tests/unit/test_scheduler.py -v
```
