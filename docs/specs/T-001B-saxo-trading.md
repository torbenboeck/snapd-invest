# T-001-B — Saxo SIM trading: placement, positions, identity backfill

**Status:** Design proposed 2026-05-16
**Supersedes scope of:** Second half of [`T-001`](../../tasks/T-001-saxo-sim-integration.md)
**Builds on:** [T-001-A](T-001A-saxo-sim-oauth-and-get-account.md) (auth + `get_account` — merged in PR #5)

---

## 1. Context

T-001-A delivered Saxo SIM **authentication** end-to-end: PKCE handshake,
encrypted token persistence, proactive + reactive refresh,
`SaxoBroker.get_account()` against `/port/v1/users/me`. Every other method
on `SaxoBroker` raises `NotImplementedError`. The engine's autonomous
MicroTrader runs on `PaperBroker` only.

T-001-B turns `SaxoBroker` into a real `IBroker` against Saxo SIM and wires
it into the existing execute pipeline. After this lands, the user can:

1. Approve an agent recommendation in the CLI and see the order land in
   their Saxo SIM portal (manually approved + executed against SIM).
2. Place an ad-hoc order via `snapdinvest place-order` (manual placement
   for testing + one-off trades).
3. See positions reconciled between engine view and Saxo's view.

MicroTrader on a SIM account remains explicitly opt-in — it's structurally
possible, but `default_account_name="paper"` keeps the autonomous loop on
paper unless the user changes the setting.

Live trading stays hard-blocked. Both code (`Settings._validate_saxo_env`)
and the harness (`.claude/hooks/pre_tool_bash.py`) reject `SAXO_ENV=live`.

The full endpoint catalog + sample bodies are in
[`docs/integrations/saxo-openapi-notes.md`](../integrations/saxo-openapi-notes.md).
This spec assumes that doc as background and won't repeat its tables.

---

## 2. Scope

### In scope

- **Identity backfill.** After the existing OAuth callback succeeds, call
  Saxo `/clients/me` and `/accounts/me` and populate `Account.saxo_client_key`
  and `Account.saxo_account_key` on the row that initiated the handshake.
- **Instrument extensions.** `Instrument` ORM model gains `saxo_uic`,
  `saxo_asset_type`, `saxo_currency_decimals` (nullable). Migration 0006.
- **`ensure_saxo_instrument` helper.** Resolves Symbol@Exchange → Saxo UIC
  via `/ref/v1/instruments` search, caches in the DB.
- **`SaxoBroker.get_last_price`** against `/trade/v1/infoprices/list`.
  Returns mid of bid/ask as `Decimal`.
- **`SaxoBroker.place_order`** (market + limit) against `POST /trade/v2/orders`.
  Idempotency-key plumbed through Saxo's `ExternalReference` field.
- **`SaxoBroker.cancel_order(order_id, account_key)`** via `DELETE /trade/v2/orders/{id}`.
- **`SaxoBroker.get_open_orders()`** via `GET /port/v1/orders/me`.
- **`SaxoBroker.get_positions(account)`** via `GET /port/v1/positions`,
  reconciling against our `Position` rows for the sim account.
- **Typed `OrderResult` discriminated union** replacing the
  `BrokerError`-only error surface for placement (per T-001-A spec §4.4
  "T-001-B foreshadowing").
- **Full `BrokerFactory` integration.** `execute_signal` /
  `execute_signals` accept a `BrokerFactory` rather than a fixed `IBroker`;
  `pipeline.run_microtrader_once`, `recommendation.approve_and_execute`,
  the scheduler closures all pass it through. Tests that wire
  `PaperBroker` directly use a `lambda _account: paper_broker` adapter.
- **`PromotionGate` callable** consulted before any sim placement.
  Trivial implementation for MVP (allow if tokens are present and not in
  refresh-failure backoff). Structure in place for eval-thresholded
  gates later.
- **CLI `snapdinvest place-order`** command for ad-hoc manual placement.
- **Engine route `POST /v1/orders`** powering the new CLI command and
  any future programmatic placement flow.
- **SIM-live integration test** for one round-trip: place a tiny limit
  order well off the market, verify it appears in `/orders/me`, cancel
  it. Env-gated by `SAXO_RUN_LIVE_TESTS=1`.
- **ADR-006** capturing the typed-discriminated-union choice for
  placement outcomes and the idempotency mapping.
- **Docs updates** in `saxo-openapi-notes.md` (corrections + additions
  observed during implementation) and `tasks/T-001-saxo-sim-integration.md`
  marked done.

### Deferred to a later task

- WebSocket streaming for live prices / position updates. T-001-B is
  polling-only.
- Asset types beyond FxSpot end-to-end. The schema supports any string
  in `saxo_asset_type`, but only FxSpot is exercised by integration tests.
  Stocks / ETFs / options follow as separate small tasks.
- Eval-thresholded promotion gates. `PromotionGate` is in place but only
  the trivial "allow if authenticated" rule is implemented.
- Auto-re-running `auth saxo` from the CLI when the engine returns 401
  (added by the parallel `bugfix/saxo-401-needs-reauth` PR). T-001-B
  inherits whatever that PR ships; no extra work here.
- Tax / FIFO cost basis. Orders carry enough metadata to reconstruct
  later; the work itself is out of MVP.
- Backfilling historic Saxo positions/orders into our DB. The engine's
  view of a sim account starts the moment `SaxoBroker.place_order` first
  runs in T-001-B.
- Bracket / OCO / stop / trailing-stop orders. Market + limit only.

### Hard-blocked

- Live trading. `SAXO_ENV=live` remains rejected. ADR-005 stands.
- Order placement from autonomous MicroTrader against a sim account
  **without explicit user opt-in.** MicroTrader runs against
  `Settings.default_account_name` (defaults to `paper`). To run
  autonomously on sim, the user changes that setting deliberately. We
  don't add a CLI command for it in T-001-B.

---

## 3. User actions (one-time)

1. **Enable "Allow Trading: Yes" on the Saxo SIM app.** The T-001-A app
   was created with trading disabled (per the original spec §3). Toggle
   it on in the developer portal at https://www.developer.saxo. No new
   app key needed.
2. **No other config changes.** `engine/.env` keeps the same OAuth values
   from T-001-A. The redirect URL stays exactly as configured (port-less
   in the portal, with `:8000` in `.env` — see ADR-005 notes).
3. **Run the existing OAuth flow once more** after merge:
   ```
   dotnet run --project cli/src/SnapdInvest.Cli -- auth saxo --account <uuid>
   ```
   The callback now ALSO populates `Account.saxo_client_key` and
   `Account.saxo_account_key`. Pre-T-001-B sim accounts whose tokens are
   still valid keep working for `get_account`; they need a re-auth (or a
   manual identity-backfill route call) before placement.

---

## 4. Architecture

### 4.1 Order flow (signal → Saxo)

```
recommendation.approve_and_execute  /  pipeline.run_microtrader_once
                       │
                       ▼
            broker_factory(account)        ← BrokerFactory in api.py
                       │
                       ▼              ┌──────────────┐
            execute_signal ──────────▶│ PromotionGate│ — allow / DeniedFor
                       │              └──────────────┘
                       ▼ (if allowed)
            ┌─────────────────┐
            │ risk gate       │
            └─────────────────┘
                       │ (if allowed)
                       ▼
            SaxoBroker.place_order
                       │
            ┌──────────┴──────────┐
            │ POST /trade/v2/orders
            │   body = mapped OrderRequest
            │   ExternalReference = idempotency_key
            └──────────┬──────────┘
                       ▼
            OrderResult discriminated union
                       │
                       ▼
            execute_signal returns ExecutionOutcome
                       │
                       ▼
            audit + persisted Order/Trade rows
```

### 4.2 OrderResult discriminated union

Per T-001-A §4.4 foreshadowing: placement has many meaningful outcomes
the caller has to discriminate. Exceptions don't fit. Replace
`BrokerHttpError`-on-placement with a `Literal`-tagged discriminated
union and let `match` + `assert_never` enforce exhaustive handling.

```python
# In engine/src/snapd_invest/broker/__init__.py

@dataclass(slots=True, frozen=True)
class Filled:
    kind: Literal["filled"] = "filled"
    order: Order
    trades: list[Trade]


@dataclass(slots=True, frozen=True)
class PartiallyFilled:
    kind: Literal["partially_filled"] = "partially_filled"
    order: Order
    trades: list[Trade]
    remaining_quantity: Decimal


@dataclass(slots=True, frozen=True)
class Rejected:
    kind: Literal["rejected"] = "rejected"
    reason: str
    saxo_error_code: str | None


@dataclass(slots=True, frozen=True)
class BrokerDown:
    kind: Literal["broker_down"] = "broker_down"
    detail: str


@dataclass(slots=True, frozen=True)
class IdempotentReplay:
    kind: Literal["idempotent_replay"] = "idempotent_replay"
    order: Order
    trades: list[Trade]
    original_idempotency_key: str


OrderResult = Filled | PartiallyFilled | Rejected | BrokerDown | IdempotentReplay
```

Call sites use `match`:

```python
match result:
    case Filled(order=o, trades=ts) | PartiallyFilled(order=o, trades=ts):
        await record_event(session, clock, event_type="order_placed", ...)
    case Rejected(reason=r, saxo_error_code=code):
        await record_event(session, clock, event_type="order_rejected", ...)
    case BrokerDown(detail=d):
        # transient — caller decides retry vs abort
        raise BrokerHttpError(503, d)
    case IdempotentReplay(order=o, trades=ts, original_idempotency_key=k):
        await record_event(session, clock, event_type="order_idempotent_replay", ...)
    case _:
        from typing import assert_never
        assert_never(result)
```

`FillResult` (the T-001-A return type of `PaperBroker.place_order`) is
**replaced** by `OrderResult` everywhere. PaperBroker's existing logic maps
straightforwardly: it always returns `Filled` (no partial fills against
the in-memory bar) or `Rejected` (no last price, or limit not marketable),
or `IdempotentReplay` (duplicate idempotency key).

**Why a tagged union and not subclassing?** Because `match` on a sealed
union with `assert_never` gives compiler-checked exhaustiveness; subclass
dispatch via `isinstance` doesn't. Same reason FastAPI uses tagged unions
in `Annotated[Union[...], Field(discriminator="kind")]`.

`BrokerError` (and its hierarchy) is preserved for non-placement failures
(auth, timeouts on read paths, etc.). T-001-A's `BrokerAuthError` and
`BrokerHttpError` remain in use for `get_account`, `get_last_price`,
`get_positions`, `cancel_order`, `get_open_orders` — i.e. read paths and
side-effect-free operations where there really are only two outcomes
(got it or didn't).

### 4.3 Idempotency mapping

Our `OrderRequest.idempotency_key` (32-char SHA-256 hash from
`execution._make_idempotency_key`) maps to Saxo's `ExternalReference`
field in the order body:

```json
{
  "Uic": 16,
  "BuySell": "Buy",
  ...
  "ExternalReference": "abcd1234...32chars"
}
```

Saxo accepts up to 50 chars in `ExternalReference`. If we observe a
duplicate `idempotency_key` on placement, the SaxoBroker:

1. Checks for an existing `Order` row by `idempotency_key`.
2. If found and `Order.status` is terminal (`filled`, `rejected`,
   `cancelled`), returns `IdempotentReplay(order=existing, ...)` without
   calling Saxo at all.
3. If found but pending (e.g. previous attempt hit a network glitch),
   queries Saxo's `/port/v1/orders/me?ExternalReference=<key>` to find
   the existing remote order, reconciles status into our DB, returns
   `IdempotentReplay` or the appropriate updated state.
4. If not found, places the order with `ExternalReference=idempotency_key`
   and persists the resulting `Order` row.

Saxo's exact behavior when `ExternalReference` collides is **TBD at
implementation time** — verify with their docs / a deliberate
double-submission test.

### 4.4 Identity backfill

After the existing `/v1/oauth/saxo/callback` route completes the token
exchange, it makes two additional calls (using the freshly-stored access
token):

1. `GET /port/v1/clients/me` → `ClientKey`, `DefaultAccountId`
2. `GET /port/v1/accounts/me` → list of `(AccountKey, AccountId, …)`

Selection rule:
- If `Account.saxo_account_id` was set at `create-account` time
  (matches the human Saxo account number, e.g. `22264911`), pick the
  matching row's `AccountKey`.
- Otherwise, pick the row whose `AccountId == DefaultAccountId`.
- Persist `ClientKey` → `Account.saxo_client_key`, `AccountKey` →
  `Account.saxo_account_key`. Idempotent — running auth again just
  re-writes the same values.

Backfill failure (e.g. Saxo returns 401 on these calls) is logged as an
audit event but does NOT fail the callback. The tokens are stored,
`get_account` works, the user can re-trigger backfill explicitly.

Folded into the existing callback rather than a separate
`/v1/oauth/saxo/finalize` route — one round-trip is simpler than two,
and the backfill is cheap and re-runnable.

### 4.5 Instrument extensions

```sql
-- migration 0006
ALTER TABLE instruments ADD COLUMN saxo_uic INTEGER NULL;
ALTER TABLE instruments ADD COLUMN saxo_asset_type VARCHAR(16) NULL;
ALTER TABLE instruments ADD COLUMN saxo_currency_decimals INTEGER NULL;
```

`ensure_saxo_instrument(session, broker, *, symbol, exchange)`:

1. Looks up the `Instrument` by `(symbol, exchange)`.
2. If it has a non-null `saxo_uic`, return it.
3. Otherwise, call `broker.search_instruments(symbol)` →
   `GET /ref/v1/instruments?KeyWords={symbol}&AssetTypes={inferred}`.
4. Match the result whose `Symbol == symbol`. Persist `saxo_uic`,
   `saxo_asset_type`, and (eventually) `saxo_currency_decimals`.
5. Return the updated instrument.

For T-001-B's manual placement flow, the user passes
`--symbol EURDKK@FX` on the CLI. The `@FX` exchange suffix maps to
`AssetType=FxSpot`. For stocks / ETFs the mapping is `@<exchange code>
→ AssetType=Stock`. Helper lookup table in `engine/src/snapd_invest/data.py`.

### 4.6 Promotion gate

```python
# In engine/src/snapd_invest/risk.py or a new engine/src/snapd_invest/promotion.py

@dataclass(slots=True, frozen=True)
class Allowed:
    kind: Literal["allowed"] = "allowed"


@dataclass(slots=True, frozen=True)
class DeniedFor:
    kind: Literal["denied"] = "denied"
    reason: str


PromotionDecision = Allowed | DeniedFor
PromotionGate = Callable[[Account, IBroker], PromotionDecision]


def trivial_promotion_gate(account: Account, broker: IBroker) -> PromotionDecision:
    """MVP: paper always; sim if tokens present and last refresh succeeded."""
    if account.account_type == "paper":
        return Allowed()
    if account.account_type == "sim":
        # Liveness check: broker.get_account() returning Filled-equivalent
        # without auth error implies the token stack is healthy. Don't call
        # it here per request — too expensive. The cheap proxy: tokens
        # exist for this account+broker AND we haven't seen a recent
        # refresh failure (TBD: refresh-failure cache, possibly a new
        # column on `oauth_tokens`).
        return Allowed()  # placeholder for now
    return DeniedFor(reason=f"unsupported account_type: {account.account_type}")
```

The gate is called by `execute_signal` BEFORE the risk gate, but only for
sim/live accounts (paper short-circuits to `Allowed()`). T-001-B ships the
trivial implementation. Eval-thresholded promotion is a separate later
task that just swaps the function pointer.

### 4.7 Position reconciliation

`SaxoBroker.get_positions(account)` calls
`GET /port/v1/positions?ClientKey=<key>&FieldGroups=...` and returns the
list of Saxo's positions. The engine's `Position` rows for sim accounts
are then reconciled:

- For each Saxo position with `(AccountKey, Uic)`, find our row by
  `(account_id, instrument_id_resolved_from_uic)`.
- If quantities match: no-op.
- If our row says X and Saxo says Y: log a structured
  `position_drift` audit event, update our row to Y (Saxo is the source
  of truth for sim accounts), and return.
- If Saxo has a position we don't track: create a `Position` row. Set
  `tag="view_only"` initially — the user explicitly opts in to "managed"
  before any agent can act on it.
- If we track a position Saxo doesn't: this means we placed an order via
  Saxo but the position is now flat (sold elsewhere or expired). Mark our
  row's quantity to 0; the row stays for history.

Reconciliation runs:
- After every `place_order` against sim.
- On demand via `GET /v1/portfolio?account_id=<sim_uuid>` (existing route
  extended to call reconciliation for sim accounts).
- Periodically? **Open question** — see §7.

### 4.8 Updated code structure

```
engine/src/snapd_invest/
├── broker/
│   ├── __init__.py        # adds OrderResult union, PromotionGate type
│   ├── paper.py           # PaperBroker.place_order now returns OrderResult
│   ├── saxo.py            # place_order, cancel_order, get_open_orders,
│   │                      #   get_positions, get_last_price, search_instruments
│   └── saxo_oauth.py      # unchanged + new fetch_client_info, fetch_accounts_info
├── promotion.py           # new: PromotionGate + trivial impl
├── execution.py           # signature changes: BrokerFactory + PromotionGate
├── pipeline.py            # pass through
├── recommendation.py      # pass through
├── scheduler.py           # pass through
├── data.py                # ensure_saxo_instrument helper
├── models.py              # Instrument.saxo_uic / .saxo_asset_type / .saxo_currency_decimals
└── api.py                 # POST /v1/orders, backfill in /oauth/saxo/callback,
                           #   broker_factory_dep already exists from T-001-A
```

### 4.9 Multi-user readiness

All new persistence stays per `(account_id, broker)` like T-001-A. Order
placement and reconciliation never query "all sim orders" — always scoped
by account. The `Position.tag` story (`managed | view_only | untouchable`)
already supports per-position user policies; T-001-B reuses it.

---

## 5. Test strategy

| Test file | Coverage focus |
|---|---|
| `tests/unit/test_saxo_broker.py` | Extended: `get_last_price` happy path + currency mismatch; `place_order` market + limit happy paths + idempotent replay + Rejected + BrokerDown; `cancel_order` happy + 404; `get_open_orders` happy + empty; `get_positions` reconciliation cases (match, drift, new, gone); reactive refresh on 401 for all of place/cancel/get-positions. |
| `tests/unit/test_order_result.py` (new) | Pattern-match exhaustiveness on each `OrderResult` variant; `assert_never` catches missing case. |
| `tests/unit/test_promotion.py` (new) | Trivial gate: paper allowed always; sim allowed for now; unsupported types denied. |
| `tests/unit/test_data.py` (extended) | `ensure_saxo_instrument` cache hit, cache miss + Saxo search, no-match raises. |
| `tests/unit/test_execution.py` (refactored) | All existing tests pass `lambda _: paper_broker` adapter where they used to pass `paper_broker` directly. New: PromotionGate denial short-circuits execution. |
| `tests/unit/test_api_oauth.py` (extended) | OAuth callback also populates `Account.saxo_client_key` + `saxo_account_key`. Identity backfill failure logged but doesn't fail callback. |
| `tests/unit/test_api_orders.py` (new) | `POST /v1/orders` happy + risk-gate denial + promotion-gate denial + 404 on unknown account. |
| `tests/integration/test_saxo_live.py` (extended) | Place a tiny EURDKK limit order well off the market via `SaxoBroker.place_order`; verify it shows in `/orders/me`; cancel; verify it's gone. Marked `@pytest.mark.saxo_live`. |
| `cli/tests/SnapdInvest.Cli.Tests.Unit/Commands/PlaceOrderCommandTests.cs` (new) | Happy path; required-arg validation; non-zero exit on engine error. |

Coverage targets:
- `broker/saxo.py` ≥ 90%.
- `promotion.py` 100%.
- New order-result + ensure_saxo_instrument code 100%.

---

## 6. Acceptance criteria

- [ ] After OAuth, `Account.saxo_client_key` and `Account.saxo_account_key`
      are populated on the row.
- [ ] `SaxoBroker.get_last_price(EURDKK@FX)` returns a `Decimal` close to
      the value the Saxo portal shows for EURDKK.
- [ ] `SaxoBroker.place_order` places a market or limit order against
      Saxo SIM. Returns a typed `OrderResult`.
- [ ] Idempotency: replaying the same `idempotency_key` returns
      `IdempotentReplay(...)` without a second Saxo call.
- [ ] `SaxoBroker.cancel_order(order_id, account_key)` removes an open
      order; verified by absence in `/orders/me`.
- [ ] `SaxoBroker.get_positions(account)` reconciles our `Position` rows
      with Saxo's view; drift logged as an audit event.
- [ ] `execute_signal` accepts a `BrokerFactory`. All existing tests pass.
- [ ] `PromotionGate` is consulted before any sim placement. Trivial impl
      lets paper through always, sim through when tokens are present.
- [ ] CLI `snapdinvest place-order --account <uuid> --symbol EURDKK@FX
      --side buy --qty 1000 --type market` places a market order via
      `POST /v1/orders`.
- [ ] SIM-live test (`SAXO_RUN_LIVE_TESTS=1 make test-engine-live`)
      places + cancels a tiny limit order end-to-end.
- [ ] `make test`, `make lint`, `cd engine && uv run mypy src`,
      `cd cli && dotnet build -p:TreatWarningsAsErrors=true && dotnet test
      && dotnet format --verify-no-changes` all clean.
- [ ] ADR-006 appended.
- [ ] `tasks/T-001-saxo-sim-integration.md` status flipped to `done`.

---

## 7. Open questions

- **Exact `ExternalReference` semantics.** Saxo's docs are vague on
  whether a duplicate `ExternalReference` returns 200 with the existing
  order, 409, or just silently creates a duplicate. Verify with a
  deliberate test in the early implementation tasks.
- **Position reconciliation cadence.** Run after every `place_order` and
  on `/v1/portfolio` (decided in §4.7). Do we also need a periodic
  reconciliation tick to catch external changes (manual edits in Saxo
  portal, fills from sister orders, etc.)? Probably yes, eventually — but
  not in T-001-B. Reconciling on every portfolio read is sufficient at
  MVP scale.
- **`saxo_currency_decimals` storage.** Saxo returns per-instrument
  decimal precision (e.g. EURDKK=4). We could store this on `Instrument`
  and use it for price formatting. Adds noise to the model for limited
  benefit at MVP — defer to first instrument-display task.
- **Behavior when promotion gate denies.** Currently the gate returns
  `Allowed` for sim accounts unconditionally. When eval-thresholded
  gates land later, what does `execute_signal` do on `DeniedFor`?
  Probably: same as risk-gate denial — audit event + return
  `ExecutionOutcome` with `gate_allowed=False`. Decided when needed.
- **Position drift handling for "managed" positions.** Saxo says we have
  100 shares, our DB says 80 (because we placed a partial fill we missed
  reconciling). Do we trust Saxo and update? §4.7 says yes for view_only.
  For managed: probably yes too (Saxo is the bank's ledger, we're the
  cache). Audit event lets us notice if drift is suspiciously frequent.

---

## 8. Verify

```bash
make test                                              # all unit + integration (skip 1)
make lint
cd engine && uv run mypy src
cd cli && dotnet build -p:TreatWarningsAsErrors=true && dotnet test

# Opt-in live test (requires SIM creds + a SIM account with tokens):
make test-engine-live
```

Manual smoke (post-merge):

```bash
# Re-run auth once to populate saxo_client_key + saxo_account_key:
dotnet run --project cli/src/SnapdInvest.Cli -- auth saxo --account <uuid>

# Place a small market order:
dotnet run --project cli/src/SnapdInvest.Cli -- place-order \
    --account <uuid> --symbol EURDKK@FX --side buy \
    --qty 1000 --type market

# Verify in the Saxo SIM portal that the position appears.
```
