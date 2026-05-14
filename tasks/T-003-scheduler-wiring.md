# T-003 — Wire the APScheduler into FastAPI lifespan

**Status:** done
**Created:** 2026-05-12
**Completed:** 2026-05-14
**Owner:** Claude Code
**Blocked by:** —  (initial declaration of `T-002` was incorrect; the integration test uses pre-seeded bars / `FakeLlmProvider`, not real market data)

## Context

`scheduler.py` defines the scheduler factory but the FastAPI app doesn't start it. We need:

- MicroTrader tick every minute (configurable)
- Agent run every 30 minutes (configurable)
- Market-data refresh every N seconds (configurable; default 60s)
- Recommendation `expire_overdue` every 5 minutes

All jobs must use the same handler functions exposed by the HTTP API, so manual `/run-once` and scheduled execution share one code path.

## Acceptance criteria

- [x] FastAPI `lifespan` starts and stops the scheduler cleanly
- [x] Job intervals configurable via `Settings` (minutes for each job)
- [x] Jobs use `coalesce=True` and `max_instances=1` (already set in `build_scheduler`)
- [x] Manual `POST /v1/run-once` and scheduled tick share the same handler (both call `pipeline.run_microtrader_once`)
- [x] Errors inside a job are logged, never crash the scheduler (per-handler `try/except` + `EVENT_JOB_ERROR` backstop)
- [x] When `Settings.scheduler_enabled=False`, the scheduler is NOT started (useful for tests and dev)
- [x] Integration test proves the scheduler → handler wiring runs end-to-end (`test_scheduler_fires_handler` + `test_scheduler_survives_handler_exception`). Real data-flow E2E is deferred to T-004 once T-002 lands `yfinance` provider.

## Files in scope

- `engine/src/snapd_invest/scheduler.py`
- `engine/src/snapd_invest/api.py`
- `engine/src/snapd_invest/config.py`
- `engine/src/snapd_invest/pipeline.py` (new — per design spec)
- `engine/src/snapd_invest/logging_config.py` (drive-by fix: `format_exc_info` only in JSON branch)
- `engine/tests/unit/test_scheduler.py` (new)
- `engine/tests/unit/test_pipeline.py` (new)
- `engine/tests/unit/test_api_health.py` (lifespan tests added)
- `engine/.env.example`, `docs/architecture/module-map.md` (docs)

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
