# T-001 — Saxo SIM integration

**Status:** done
**Superseded by:** T-001-A (merged in PR #5 — auth + `get_account`) + T-001-B (placement, idempotency, BrokerFactory + PromotionGate threading, position reconciliation, manual `place-order` CLI)
**Created:** 2026-05-12
**Owner:** Claude Code
**Blocked by:** —
**Closed:** 2026-05-21

> ⚠️ **Note:** This task was drafted before OAuth research and incorrectly named
> `client_credentials` as the grant type. Saxo does not support that flow for
> retail developers. The corrected design is in
> [`docs/specs/T-001A-saxo-sim-oauth-and-get-account.md`](../docs/specs/T-001A-saxo-sim-oauth-and-get-account.md)
> and uses Authorization Code Grant + PKCE. The original acceptance criteria
> below remain as a reference for T-001-B.

## Context

MVP currently has only the internal `PaperBroker`. The user already has a Saxo account; Nordnet does not onboard API customers, so Saxo is the production target. Saxo provides a separate SIM environment with the same API surface as live, on different auth + base URLs.

Adding `SaxoBroker` (targeting SIM) is the next external-system integration. It must follow `IBroker` exactly so swapping brokers per-agent or per-strategy is a config change, not a refactor.

## Acceptance criteria

- [ ] `SaxoBroker` class in `engine/src/snapd_invest/broker.py` implementing `IBroker`
- [ ] OAuth2 client_credentials flow against Saxo SIM auth endpoint, with token refresh
- [ ] All Saxo HTTP calls go through a single `httpx.AsyncClient` instance, configurable timeout
- [ ] `engine/.env.example` updated with `SAXO_ENV`, `SAXO_CLIENT_ID`, `SAXO_CLIENT_SECRET`
- [ ] `Settings` extended with these fields (optional, validated when present)
- [ ] At least the following Saxo operations wrapped:
  - place market order
  - place limit order
  - cancel order
  - get account
  - get positions
  - get last price for an instrument
- [ ] Idempotency-key handling layered on top of Saxo's `ExternalReference` field
- [ ] `agent.environment == "sim"` means broker selection resolves to `SaxoBroker`; `paper` stays on `PaperBroker`
- [ ] Unit tests against a mocked `httpx.AsyncClient` (use `respx`)
- [ ] Integration test gated by env var `SAXO_RUN_LIVE_TESTS=1` and skipped in CI
- [ ] ADR added to `docs/architecture/decision-log.md`

## Files in scope

- `engine/src/snapd_invest/broker.py`
- `engine/src/snapd_invest/config.py`
- `engine/.env.example`
- `engine/tests/unit/test_saxo_broker.py` (new)
- `docs/architecture/decision-log.md`
- `docs/ubiquitous-language.md` (if any new terms)

## Out of scope

- Saxo live trading. Hard-disabled. `SAXO_ENV=live` is blocked by the `.claude/hooks/pre_tool_bash.py` hook.
- Order types beyond market + limit.
- Streaming / WebSocket subscriptions (polling only).

## Verify

```bash
cd engine
uv run ruff check
uv run mypy src
uv run pytest tests/unit/test_saxo_broker.py -v
uv run pytest  # full suite still green
```

## Notes

Saxo OpenAPI reference: <https://www.developer.saxo/openapi/learn>.
Saxo SIM and live use different `gateway.saxobank.com` paths and different `logonvalidation.net` hostnames — never share credentials or env between them.
