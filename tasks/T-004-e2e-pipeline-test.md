# T-004 — End-to-end pipeline test

**Status:** pending
**Created:** 2026-05-12
**Owner:** Claude Code
**Blocked by:** —

## Context

We have unit tests for every module. We need at least one end-to-end test that exercises the full pipeline through the FastAPI surface:

1. Boot app with in-memory SQLite
2. Seed bars for AAPL@NASDAQ
3. POST `/v1/run-once` → expect 200 and a deterministic outcome
4. GET `/v1/portfolio` → expect resulting position
5. GET `/v1/audit` → expect signal_emitted, risk_decision, order_placed events
6. POST `/v1/agents/run` with FakeLlmProvider (overridden in DI) → expect recommendation created
7. POST `/v1/recommendations/{id}/approve` → expect execution outcome
8. Final portfolio reflects both executions

This is the smoke test that catches integration regressions.

## Acceptance criteria

- [ ] Test file `engine/tests/e2e/test_full_pipeline.py`
- [ ] Uses `httpx.ASGITransport(app=app)` — no real network
- [ ] Overrides DI dependencies: in-memory SQLite, FakeClock, FakeLlmProvider with pre-canned response
- [ ] Asserts on portfolio state, audit chain, recommendation lifecycle
- [ ] Runs in CI

## Files in scope

- `engine/tests/e2e/__init__.py` (new)
- `engine/tests/e2e/test_full_pipeline.py` (new)
- `engine/tests/conftest.py` (extend with E2E fixtures if needed)

## Out of scope

- Performance / load tests
- Tests against real Saxo SIM
- Browser / UI tests

## Verify

```bash
cd engine
uv run pytest tests/e2e -v
```
