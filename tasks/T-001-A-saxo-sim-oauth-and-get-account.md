# T-001-A — Saxo SIM OAuth + `get_account`

**Status:** done
**Created:** 2026-05-14
**Completed:** 2026-05-14
**Owner:** Claude Code
**Blocked by:** —
**Supersedes scope of:** First half of `T-001` (now archived in `.archive/T-001-saxo-sim-integration.md`)
**Companion (pending):** [`T-001-B-saxo-trading.md`](T-001-B-saxo-trading.md)
**Backfill note:** This task file was added retroactively on 2026-05-16 to keep the
`tasks/` queue symmetric with T-001-B and T-003. T-001-A originally shipped via
`docs/specs/T-001A-saxo-sim-oauth-and-get-account.md` +
`docs/plans/2026-05-14-T-001A-saxo-sim-oauth.md` + PR #5 without a queue file.

## Context

MVP has only `PaperBroker`. Saxo SIM is the next execution venue. Before placing
orders the engine must authenticate against Saxo's OAuth, persist the refresh
token across restarts, and demonstrate that auth works against a real Saxo
endpoint without placing trades.

The original `T-001` design used `client_credentials` — Saxo does not support
that grant for retail developers. T-001-A corrects this to **Authorization Code
Grant with PKCE** (RFC 7636), captured in ADR-005.

T-001-A delivers auth + a single read endpoint (`get_account`). T-001-B layers
order placement on top.

Full design: [`docs/specs/T-001A-saxo-sim-oauth-and-get-account.md`](../docs/specs/T-001A-saxo-sim-oauth-and-get-account.md).
TDD plan: [`docs/plans/2026-05-14-T-001A-saxo-sim-oauth.md`](../docs/plans/2026-05-14-T-001A-saxo-sim-oauth.md).

## Acceptance criteria

- [x] Authorization Code + PKCE handshake against `https://sim.logonvalidation.net/{authorize,token}`
- [x] Refresh-token persistence (encrypted at rest) with proactive + reactive refresh
- [x] `Cipher` protocol + `FernetCipher` default implementation, keyed by `SNAPDINVEST_ENCRYPTION_KEY`
- [x] `make init-keys` target (wraps `python -m snapd_invest.tools.init_keys`)
- [x] `SaxoBroker` class implementing `IBroker`, with only `get_account()` wired end-to-end
- [x] Broker selection by `Account.account_type` (`paper` → `PaperBroker`, `sim` → `SaxoBroker`, `live` blocked)
- [x] `Settings` extended with `SNAPDINVEST_SAXO_ENV`, `SNAPDINVEST_SAXO_CLIENT_ID`, `SNAPDINVEST_SAXO_REDIRECT_URI`, `SNAPDINVEST_ENCRYPTION_KEY`
- [x] Engine routes: `POST /v1/oauth/saxo/start`, `GET /v1/oauth/saxo/callback`, `GET /v1/oauth/saxo/status`
- [x] CLI commands: `snapd-invest auth saxo --account <id>`, `snapd-invest get-account`
- [x] `broker.py` refactored into a `broker/` package
- [x] Module-map updated for the `broker/` package
- [x] Unit tests for OAuth state machine, cipher, and broker `get_account`
- [x] Env-gated SIM-live test marked `@pytest.mark.saxo_live`, skipped unless `SAXO_RUN_LIVE_TESTS=1`
- [x] ADR-005 captures the OAuth-flow choice
- [x] `SAXO_ENV=live` hard-blocked in `Settings` validation and `.claude/hooks/pre_tool_bash.py`

## Files delivered (PR #5 + follow-ups)

- `engine/src/snapd_invest/broker/__init__.py`, `paper.py`, `saxo.py`, `saxo_oauth.py`
- `engine/src/snapd_invest/crypto.py`
- `engine/src/snapd_invest/tools/init_keys.py`
- `engine/src/snapd_invest/config.py` — Saxo + encryption settings, live-env guard
- `engine/src/snapd_invest/api.py` — `/v1/oauth/saxo/*`, `/v1/accounts/{id}`
- `engine/src/snapd_invest/models.py` — `OAuthState`, `OAuthToken`, `Account.saxo_*` columns
- `engine/alembic/versions/0004_oauth_schema.py`, `0005_account_saxo_identity.py`
- `engine/tests/unit/test_saxo_broker.py`, `test_saxo_oauth.py`, `test_crypto.py`, `test_init_keys.py`
- `engine/tests/integration/test_saxo_live.py`
- `cli/src/SnapdInvest.Cli/Commands/AuthSaxoCommand.cs`, `GetAccountCommand.cs`, `CreateAccountCommand.cs`
- `cli/src/SnapdInvest.Client/IEngineApi.cs` — OAuth + account endpoints
- `docs/specs/T-001A-saxo-sim-oauth-and-get-account.md`
- `docs/plans/2026-05-14-T-001A-saxo-sim-oauth.md`
- `docs/architecture/decision-log.md` — ADR-005
- `docs/integrations/saxo-openapi-notes.md`

## Out of scope (deferred to T-001-B)

- `place_order` (market + limit), `cancel`, `get_positions`, `get_last_price`
- Idempotency-key handling on top of Saxo's `ExternalReference`
- Hooking `SaxoBroker` into the MicroTrader scheduled job
- Promotion-gate evaluation beyond ADR-003's "paper always allowed"

## Hard-disabled (T-001-A and T-001-B both)

- `SAXO_ENV=live` — guarded in `Settings` validation and `.claude/hooks/pre_tool_bash.py`

## Verify

```bash
cd engine
uv run ruff check
uv run ruff format --check
uv run mypy src
uv run pytest                                       # all unit tests, integration skipped
SAXO_RUN_LIVE_TESTS=1 uv run pytest -m saxo_live -v # opt-in live test

cd ../cli
dotnet build -p:TreatWarningsAsErrors=true
dotnet test
dotnet format --verify-no-changes
```

## Follow-ups landed after PR #5

- PR #7 — fix(cli): configure Refit JSON for engine's snake_case + Decimal-as-string
- PR #9 — fix(engine,cli): graceful 401 on Saxo re-auth required
