# T-001-B — Saxo SIM trading (place_order, get_positions, get_last_price, idempotency, MicroTrader wiring)

**Status:** done
**Created:** 2026-05-15
**Owner:** Claude Code
**Blocked by:** T-001-A (merged in PR #5)

## Context

T-001-A delivered Saxo SIM auth + `SaxoBroker.get_account()` only. Order
placement, position queries, and the rest of `IBroker` raise
`NotImplementedError`. T-001-B is the second half: turn `SaxoBroker` into a
real `IBroker` against Saxo SIM and wire it into the existing execute
pipeline.

The goal is **paper trading on Saxo SIM** end-to-end — the user can review
an agent recommendation, approve it, and see the order land in their Saxo
SIM account. Live trading remains hard-blocked.

Reference material captured during T-001-A:
[`docs/integrations/saxo-openapi-notes.md`](../docs/integrations/saxo-openapi-notes.md)
covers the identity model, endpoint catalog, and pre-loaded sample bodies.

## Acceptance criteria

### Saxo identity backfill

- [ ] After successful OAuth, the engine populates `Account.saxo_client_key`
      and `Account.saxo_account_key` automatically by calling `/clients/me`
      and `/accounts/me`.
- [ ] If the Client has multiple accounts and `saxo_account_id` was supplied
      at create-account time, the matching `AccountKey` is selected;
      otherwise the `DefaultAccountId` is used.
- [ ] Either expose this as a separate `/v1/oauth/saxo/finalize` route
      called automatically on first successful auth, or fold it into the
      existing `GET /v1/oauth/saxo/callback` handler. Prefer the latter
      unless we discover a reason to split.

### `SaxoBroker.get_last_price`

- [ ] Implements `IBroker.get_last_price(session, instrument)` against
      `/trade/v1/infoprices/list?Uics=&AssetType=&Amount=&FieldGroups=...`.
- [ ] `Instrument` model gains a `saxo_uic` (int) and `saxo_asset_type`
      (str) column — required by every Saxo trading call. Migration 0006.
- [ ] An `ensure_instrument` lookup helper resolves Symbol@Exchange ↔ UIC
      via `/ref/v1/instruments?KeyWords=` on first reference, caches in DB.
- [ ] Returns the mid of bid/ask as `Decimal`. Currency mismatch raises a
      typed error (don't silently round into the account's base currency).

### `SaxoBroker.place_order`

- [ ] Replaces the T-001-A NotImplementedError stub.
- [ ] Implements market and limit orders against `POST /trade/v2/orders`
      with body shaped per `docs/integrations/saxo-openapi-notes.md` (Uic,
      BuySell, AssetType, Amount, OrderType, OrderRelation=StandAlone,
      OrderDuration={GoodTillCancel|DayOrder}, AccountKey,
      ManualOrder=false for autonomous, ManualOrder=true for human-approved).
- [ ] Idempotency: passes the engine's existing `idempotency_key` through
      to Saxo's `ExternalReference` field. Verify the field name + length
      against current Saxo docs at implementation time.
- [ ] Result is a typed discriminated union (per the T-001-A spec §4.4
      "T-001-B foreshadowing"): `Filled | PartiallyFilled | Rejected(reason)
      | BrokerDown | IdempotentReplay(original_id)`. Implemented as
      `Literal`-tagged `@dataclass(slots=True, frozen=True)` types with
      `match` + `assert_never` at call sites.
- [ ] Reactive 401 refresh + retry once (already implemented in
      `SaxoBroker._authed_get`; extend the same pattern to `_authed_post`).

### `SaxoBroker.get_positions` (read-only)

- [ ] Returns the engine's view: a list of `Position` rows reconciled
      against `GET /port/v1/positions?ClientKey=&FieldGroups=...`.
- [ ] On reconciliation conflict (engine has 100, Saxo has 80), log a
      structured `position_drift` audit event and trust Saxo as the source
      of truth — the engine's `Position` rows are a cache of Saxo state for
      `sim` accounts, not the ledger.

### Cancel + status

- [ ] `SaxoBroker.cancel_order(order_id)` wraps `DELETE /trade/v2/orders/{orderId}?AccountKey=…`.
- [ ] `SaxoBroker.get_open_orders()` wraps `GET /port/v1/orders/me?fieldGroups=DisplayAndFormat`.
- [ ] Both methods land in `IBroker` as new abstract methods (or in a
      separate `IBrokerOrderManagement` protocol — decide during
      implementation; avoid hypothetical splits if not needed).

### Broker-factory full integration (the deferred half of T-001-A's Task 15)

- [ ] `execute_signal` / `execute_signals` accept a `BrokerFactory` instead
      of a fixed `IBroker`. Build the broker per-signal via
      `factory(account)`.
- [ ] `pipeline.run_microtrader_once`, `recommendation.approve_and_execute`,
      and the scheduler closures all pass the factory through.
- [ ] All existing tests that wire `PaperBroker` directly switch to
      `lambda _account: paper_broker` adapters.
- [ ] `api.py`'s lifespan-built `_make_broker_factory` already exists from
      T-001-A — reuse it; no new factory construction code.

### Promotion gate

- [ ] ADR-003 says paper is "always allowed". For sim, the gate stays
      trivial at MVP (no eval thresholds yet) but the structure must be in
      place — a `PromotionGate` callable that returns `Allowed | DeniedFor(reason)`
      consulted before any sim placement. T-001-B introduces the abstraction;
      eval-thresholded gates land later.

### CLI

- [ ] `snapdinvest place-order --account <id> --symbol EURDKK@FX --side buy
      --qty 100000 --type market` for ad-hoc manual placement against the
      engine. Routes through `POST /v1/orders` (new) which calls
      `execute_signal` with `source="manual-cli"`.
- [ ] `snapdinvest positions --account <id>` calls `GET /v1/portfolio` which
      already exists; verify it works against a sim account end-to-end.

### Tests

- [ ] Unit: `tests/unit/test_saxo_broker.py` extended with `place_order`
      happy path, idempotent replay, 401-then-refresh, market-vs-limit,
      Saxo error-code parsing.
- [ ] Unit: `tests/unit/test_execution.py` updated for `BrokerFactory`.
- [ ] Integration: extend `tests/integration/test_saxo_live.py` with one
      live placement test (`SAXO_RUN_LIVE_TESTS=1`) — places a tiny limit
      order well off the market, verifies it shows in `/orders/me`, then
      cancels.
- [ ] All existing engine + cli suites stay green.

### Docs

- [ ] ADR-006 — Saxo trading: order shape choices (market vs limit; how
      we map our `OrderRequest` to Saxo's body; idempotency strategy).
- [ ] Update `docs/integrations/saxo-openapi-notes.md` with anything we
      learn during implementation (especially around `ExternalReference`,
      error shapes, asset types beyond FxSpot if we add any).
- [ ] Update `tasks/T-001-saxo-sim-integration.md` to mark T-001-B done
      (alongside T-001-A) when this PR merges.

## Files in scope

- `engine/src/snapd_invest/broker/saxo.py` — flesh out place_order, get_positions, get_last_price, cancel
- `engine/src/snapd_invest/broker/__init__.py` — extend `IBroker` if we add cancel/get_open_orders methods to the protocol
- `engine/src/snapd_invest/models.py` — `Instrument.saxo_uic`, `saxo_asset_type`
- `engine/alembic/versions/2026_NN_NN_HHMM-0006_instrument_saxo_identity.py`
- `engine/src/snapd_invest/portfolio.py` — `ensure_instrument` Saxo lookup
- `engine/src/snapd_invest/execution.py` — `BrokerFactory` parameter
- `engine/src/snapd_invest/pipeline.py` — pass factory through
- `engine/src/snapd_invest/recommendation.py` — pass factory through
- `engine/src/snapd_invest/scheduler.py` — pass factory through
- `engine/src/snapd_invest/api.py` — `/v1/orders`, optional `/v1/oauth/saxo/finalize`
- `engine/tests/unit/test_saxo_broker.py` — extend
- `engine/tests/unit/test_execution.py` — adapt to factory
- `engine/tests/integration/test_saxo_live.py` — extend with placement test
- `cli/src/SnapdInvest.Client/IEngineApi.cs` — add `PlaceOrderAsync`
- `cli/src/SnapdInvest.Client/Models/` — order DTOs
- `cli/src/SnapdInvest.Cli/Commands/PlaceOrderCommand.cs` (new)
- `cli/tests/SnapdInvest.Cli.Tests.Unit/Commands/PlaceOrderCommandTests.cs` (new)
- `docs/architecture/decision-log.md` — ADR-006
- `docs/integrations/saxo-openapi-notes.md` — corrections + additions
- `docs/specs/T-001B-saxo-trading.md` (new spec, written before plan)
- `docs/plans/YYYY-MM-DD-T-001-B-saxo-trading.md` (TDD plan, written before code)

## Out of scope

- **Live trading.** `SAXO_ENV=live` stays hard-blocked.
- Asset types beyond FxSpot. We add the Asset type field on `Instrument`
  but only test FxSpot end-to-end. Stocks / ETFs / options follow.
- WebSocket streaming. Polling only at MVP.
- Tax / FIFO cost basis. Tracked separately for the long term; orders
  carry enough metadata to reconstruct later.
- Backfilling historic Saxo positions/orders into our DB. T-001-B starts
  the engine's view of positions from the moment SaxoBroker first runs.

## Verify

```bash
cd engine
uv run ruff check && uv run ruff format --check
uv run mypy src
uv run alembic upgrade head
uv run pytest                                                       # all unit + integration (skip 1)
SAXO_RUN_LIVE_TESTS=1 uv run pytest -m saxo_live -v                 # opt-in live test

cd ../cli
dotnet build -p:TreatWarningsAsErrors=true
dotnet test
dotnet format --verify-no-changes
```

Manual smoke (post-merge):

```bash
# Same prerequisites as T-001-A: SIM account row + valid OAuth tokens.
dotnet run --project cli/src/SnapdInvest.Cli -- positions --account <uuid>
dotnet run --project cli/src/SnapdInvest.Cli -- place-order --account <uuid> \
    --symbol EURDKK@FX --side buy --qty 1000 --type market
# Verify the new position shows up in your Saxo SIM portal.
```

## Notes

- **Identity backfill design choice.** Two reasonable shapes:
  1. Fold into `/v1/oauth/saxo/callback`: after `store_tokens`, call
     `/clients/me` + `/accounts/me`, persist on `Account`. Simpler.
  2. Separate `/v1/oauth/saxo/finalize` step the CLI calls explicitly.
     More flexible for retries, but adds a round-trip.
  Default to (1) unless implementation reveals a reason to split.

- **Decimal precision.** Saxo returns prices with a per-instrument
  `Decimals` field (e.g. EURDKK = 4 → prices like `7.47385`). Don't round
  to a fixed scale on ingest; respect each instrument's `Decimals`.

- **`Amount` semantics for FxSpot.** The Saxo tutorial uses `Amount=100000`
  for EURDKK — that's 100,000 EUR (the base currency of the pair).
  Our `Quantity` field on `OrderRequest` should map directly to Saxo's
  `Amount` for FxSpot. For stocks/ETFs `Amount` is the share count.

- **Error codes worth handling explicitly** (from informal Saxo experience —
  verify against current docs):
  - `MarketClosed` — recoverable; queue the order or fail-fast based on
    `OrderDuration`.
  - `InsufficientCash` — surface to user; don't retry.
  - `InvalidUic` — instrument cache stale; refresh from `/ref/v1/instruments`.
  - `OrderNotPlaced` — generic; treat as `Rejected` with the message body.

- **Promotion gate at MVP.** Per ADR-003, the gate is trivial. For T-001-B
  the gate is "if `account_type == 'sim'` and OAuth tokens are present and
  not within refresh-failure backoff, allow". No eval thresholds yet.
  Real eval-gated promotion is a separate later task.

- **Saxo SIM developer accounts expire after ~20 days.** If the integration
  test starts failing with auth errors after a long quiet period, recreate
  the SIM developer account at https://www.developer.saxo.
