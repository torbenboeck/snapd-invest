# Saxo OpenAPI — integration notes

Reference for working with Saxo Bank's OpenAPI from the `engine`. Captures
gotchas, identity model, and the endpoint catalog. Source material:

- Saxo official PKCE sample: <https://github.com/SaxoBank/openapi-samples-csharp/tree/main/authentication/Authentication_PKCE>
- Saxo tutorials at <https://www.developer.saxo>
- ADR-005 (Authorization Code + PKCE).

This is a living document. Append + correct as we learn more.

---

## SIM vs Live

| Concern | SIM | Live |
|---|---|---|
| OAuth authorize | `https://sim.logonvalidation.net/authorize` | `https://live.logonvalidation.net/authorize` |
| OAuth token | `https://sim.logonvalidation.net/token` | `https://live.logonvalidation.net/token` |
| OpenAPI base | `https://gateway.saxobank.com/sim/openapi/` | `https://gateway.saxobank.com/openapi/` |
| Hard-blocked at MVP | no | yes — by `Settings._validate_saxo_env` and by `.claude/hooks/pre_tool_bash.py` |

T-001-A only configures SIM. The constants live in `engine/src/snapd_invest/broker/saxo_oauth.py` and `engine/src/snapd_invest/broker/saxo.py`.

---

## OAuth gotchas (the ones that cost us a session)

### 1. Redirect URL: registered without port, sent with port

**This contradicts standard OAuth 2.1 native-app guidance.** Saxo's PKCE
flow expects:

- **Registered in the developer portal:** `http://localhost/<path>` — no port,
  must use `localhost` (not `127.0.0.1`).
- **Sent in the `/authorize` request:** `http://localhost:<port>/<path>` — with
  the port your engine listens on.

Saxo's auth server matches scheme+host+path and ignores the port when
validating against the registered URL. The official C# sample picks a random
unused port at runtime, listens on it, and substitutes the port into the
redirect URL only when sending — it doesn't register the port at all.

For `snapd-invest`:
- Portal: `http://localhost/v1/oauth/saxo/callback`
- `engine/.env`: `SNAPDINVEST_SAXO_REDIRECT_URI=http://localhost:8000/v1/oauth/saxo/callback`

Mistakes that produced "Value of redirect_uri parameter is not registered":
- Registering `http://localhost:8000/v1/oauth/saxo/callback` (with port).
- Switching either side to `http://127.0.0.1:...` — Saxo's host check is exact.

### 2. App Type: Native, with Grant Type PKCE

Saxo's portal asks for both. The combination Native + PKCE is what RFC 7636
prescribes for desktop / agent apps. Pick that exact combination at app
creation; some portal builds don't let you change it later.

### 3. App Key changes when you recreate the app

Deleting and recreating the app issues a new App Key. Update
`SNAPDINVEST_SAXO_CLIENT_ID` and **restart the engine** — `uvicorn --reload`
watches `.py` files only, not `.env`.

### 4. `localhost` doesn't need to resolve to a public hostname

Saxo's auth server doesn't make any callback to your machine; only the
browser does. So you don't need to expose your engine externally — the
loopback redirect is purely a browser-to-localhost handoff.

---

## Identity model

Saxo's hierarchy:

```
Client                       (organization or individual)
 ├─ ClientKey                opaque token, stable, ~22 chars
 ├─ ClientId                 human-readable id (= AccountId for retail SIM)
 ├─ DefaultAccountId         the AccountId of the Client's primary Account
 │
 ├─ User(s)                  one or more identities under the Client
 │   ├─ UserKey              opaque
 │   ├─ UserId               human-readable
 │   └─ Name, Culture, …
 │
 └─ Account(s)               one or more sub-accounts under the Client
     ├─ AccountKey           opaque, used in trading API calls
     ├─ AccountId            human-readable (e.g. "22264911")
     └─ Active, CurrencyDecimals, …
```

For a retail SIM account the keys often collapse:

```
UserKey == ClientKey == AccountKey   (same opaque token)
UserId  == ClientId  == AccountId == DefaultAccountId
```

This is convenient but **don't assume it for live or for enterprise
clients** — they really can be distinct. Always look up the AccountKey
explicitly via `/accounts/me` rather than reusing the ClientKey.

Our `Account` row stores three of these as nullable columns (added in
migration 0005):

| Column | Saxo source | Used for |
|---|---|---|
| `saxo_client_key` | `/users/me`.ClientKey or `/clients/me`.ClientKey | scoping queries that take ClientKey |
| `saxo_account_key` | `/accounts/me[i].AccountKey` for the chosen account | **required** for placing orders (T-001-B) |
| `saxo_account_id` | `/accounts/me[i].AccountId` | display + linking to Saxo portal |

T-001-A only populates `saxo_account_id` (the human label) at create-account
time. T-001-B will backfill the opaque keys after auth by calling
`/clients/me` + `/accounts/me`.

---

## Endpoint catalog (observed in the SIM tutorial)

All paths are relative to `OpenApiBaseUrl` (= `https://gateway.saxobank.com/sim/openapi/`).
All authenticated calls require `Authorization: Bearer <access_token>`.

### T-001-A scope (read-only)

| Method | Path | Purpose | Used by |
|---|---|---|---|
| `GET` | `/port/v1/users/me` | Logged-in user — UserKey, UserId, ClientKey, Name, Culture, LastLoginTime | `SaxoBroker.get_account` (T-001-A) |

### T-001-B candidates (read)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/port/v1/clients/me` | ClientKey, ClientId, DefaultAccountId, DefaultCurrency |
| `GET` | `/port/v1/accounts/me` | List of accounts → backfill `saxo_account_key` |
| `GET` | `/port/v1/balances?AccountKey=&ClientKey=` | CashBalance, TotalValue, MarginAvailableForTrading |
| `GET` | `/port/v1/positions?ClientKey=&FieldGroups=…` | Open positions |
| `GET` | `/port/v1/orders/me?fieldGroups=DisplayAndFormat` | Open orders |
| `GET` | `/ref/v1/instruments?KeyWords=…&AssetTypes=…` | Instrument search → `Identifier` is the UIC |
| `GET` | `/trade/v1/infoprices/list?AccountKey=&Uics=&AssetType=&Amount=&FieldGroups=…` | Live(-ish) bid/ask for a list of UICs |

### T-001-B placement

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/trade/v2/orders` | Place an order |
| `PATCH` | `/trade/v2/orders` | Modify an existing order (e.g. limit → market) |
| `DELETE` | `/trade/v2/orders/{orderId}?AccountKey=…` | Cancel an order (per Saxo docs) |

#### Sample order body — limit order

```json
{
  "Uic": 16,
  "BuySell": "Buy",
  "AssetType": "FxSpot",
  "Amount": 100000,
  "OrderPrice": 7,
  "OrderType": "Limit",
  "OrderRelation": "StandAlone",
  "ManualOrder": true,
  "OrderDuration": { "DurationType": "GoodTillCancel" },
  "AccountKey": "<the AccountKey>"
}
```

Response: `{ "OrderId": "5038292933" }`.

#### Sample modify body — limit → market

```json
{
  "OrderType": "Market",
  "OrderDuration": { "DurationType": "DayOrder" },
  "AccountKey": "<the AccountKey>",
  "OrderId": "5038292934",
  "AssetType": "FxSpot"
}
```

Notes for T-001-B:
- `Uic` is the Universal Instrument Code; obtain via `/ref/v1/instruments` search.
- `AssetType` is required on every order-related call.
- `ManualOrder: true` flags the order as manually entered (vs algorithmic).
  Set to `false` for MicroTrader-driven flow.
- `OrderRelation: "StandAlone"` for single orders. Bracket / OCO orders use
  other values — out of scope for first cut.
- For `OrderDuration`, `GoodTillCancel` lives until cancelled; `DayOrder`
  expires at end of trading session.

---

## Idempotency

Saxo's order-placement endpoint accepts an `ExternalReference` field (string
up to ~50 chars). T-001-B will use this to plumb our internal idempotency
key through to Saxo so retries don't double-fill.

Reference: <https://www.developer.saxo/openapi/learn/order-types-and-modifications>

(Verify the exact field name + length when implementing — Saxo has changed
the surface here over time.)

---

## Error shape

Saxo errors generally come back as either:

```json
{ "ErrorCode": "InvalidRequest", "Message": "Account is not active" }
```

or for OAuth specifically:

```json
{ "error": "invalid_request",
  "error_description": "Value of redirect_uri parameter is not registered",
  "error_uri": null }
```

Our `SaxoBroker._authed_get` doesn't try to parse Saxo's error JSON yet —
it just wraps the HTTP response text into `BrokerHttpError`. T-001-B should
add a structured parser for the trading-side errors so the risk gate /
retry logic can dispatch on `ErrorCode`.

---

## Token lifetime gotcha

Saxo SIM refresh tokens expire much faster than the developer portal suggests
— in practice, sessions die after only a few hours, not the nominal 24h the
`refresh_token_expires_in` field reports. The result: a SIM account that was
working fine in the morning starts failing in the afternoon with no obvious
cause.

The engine surfaces this via `BrokerAuthError` from
`SaxoBroker._authed_get` (no stored tokens, refresh rejected, or 401 that
persists after refresh). The `/v1/accounts/{id}` route catches this and
returns:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{
  "detail": {
    "code": "saxo_reauth_required",
    "message": "Saxo session expired or never authenticated; run 'snapdinvest auth saxo --account <id>'",
    "account_id": "<the account id>"
  }
}
```

The CLI recognises `detail.code == "saxo_reauth_required"` on a 401 and
prints the actionable command instead of a stack trace:

```
Saxo session expired or never authenticated.
Run: snapdinvest auth saxo --account <id>
```

T-001-B will apply the same treatment to `/v1/orders` and the other
sim-aware routes — same exception, same code string, same CLI handling.

User-facing fix: re-run `snapdinvest auth saxo --account <id>` to walk
through the browser-based PKCE flow again.

---

## T-001-B lessons learned

Observed during the placement implementation; corrections + additions to
the catalog above.

### Identity backfill via the OAuth callback

After `store_tokens` succeeds, the engine immediately calls
`/port/v1/clients/me` + `/port/v1/accounts/me` and persists the result
into `Account.saxo_client_key` / `Account.saxo_account_key` /
`Account.saxo_account_id`. Failures here do **not** fail the callback —
tokens are stored either way; the user can retry by re-authenticating.

If the account row already carried a `saxo_account_id` (user-supplied at
create-account time), the matching `AccountKey` is selected from
`/accounts/me`. Otherwise the Client's `DefaultAccountId` is used. The
backfill leaves `saxo_account_key` null if no row matches — the engine
will surface `BrokerAuthError` on the next trading-side call and the
CLI prints the actionable `snapdinvest auth saxo --account <id>`.

### `ExternalReference` is our idempotency seat-belt

Saxo dedupes on `ExternalReference` (verified field name; accepts up to
~50 chars). Our 32-char SHA-256 idempotency_key fits comfortably. The
engine also checks our own DB before POSTing — terminal Orders return
`IdempotentReplay` without hitting Saxo at all. Pending rows trigger a
best-effort reconcile via `/port/v1/orders/me` then flip the row to
`filled` before returning replay.

### Synchronous placement response is minimal

`POST /trade/v2/orders` returns only `{"OrderId": "..."}` on success —
no fill price, no fill quantity. We persist the engine `Order` row with
`status="filled"` (MVP simplification) and skip Trade-row creation
entirely. Position + cash reconciliation happens via
`SaxoBroker.get_positions` (called from `build_summary` for sim
accounts), not via a fill-price echo in the placement response.

### Position reconciliation: Saxo wins

For sim accounts, `Position` rows are a cache of Saxo's view, not a
ledger. `portfolio.reconcile_sim_positions` handles four cases per
spec §4.7:

- **match** (qty + avg_cost equal): no-op.
- **drift**: update our row to Saxo's view; emit `position_drift`.
- **new** (Saxo has, we don't): create row tagged `view_only`;
  auto-create the Instrument from Saxo's response; emit
  `position_view_only_created`.
- **gone** (we have, Saxo doesn't): zero our quantity; emit
  `position_closed_externally`.

### Error shapes we actually saw

Saxo's trading endpoints can return either:

```json
{ "ErrorCode": "MarketClosed", "Message": "Market is closed" }
```

or:

```json
{ "ErrorInfo": { "ErrorCode": "...", "Message": "..." } }
```

The engine's `_parse_saxo_error` handles both flat and nested forms.
Any 4xx body that parses as an error becomes a `Rejected` outcome
carrying `saxo_error_code`; otherwise the underlying `BrokerHttpError`
propagates.

### Body shape for FxSpot orders that actually works

The sample in the original "Endpoint catalog" section is correct;
notable specifics learned during implementation:

- `Amount` for FxSpot is the **base-currency** amount (e.g. for EURDKK
  the amount is in EUR), not the notional in the account currency.
- `ManualOrder=true` for human-approved / manual orders; `false` for
  MicroTrader-driven flow. The engine derives this from
  `OrderRequest.source` (`source.startswith("manual")`).
- `OrderDuration` defaults to `DayOrder` for market orders and
  `GoodTillCancel` for limit orders. Both shapes accepted.
- httpx's encoder rejects `Decimal`, so the engine emits `Amount` and
  `OrderPrice` as `float`. Saxo accepts both int and float input.

### Cancel is per-AccountKey, not global

`DELETE /trade/v2/orders/{orderId}` requires the `AccountKey` query
parameter even though the order id is globally unique. Missing the
param returns 400, not 404.

### `_authed_request` refactor for verb-generic retry

T-001-A's `_authed_get` had reactive-refresh-on-401 logic baked into
the GET path. T-001-B extracted that into `_authed_request(method,
path, *, json=None)` and made `_authed_get` / `_authed_post` /
`_authed_delete` thin wrappers so all three reuse the same retry. Empty
response bodies (204 No Content / empty 200) return `{}` so cancel
flows don't crash `.json()`.

---

## Testing posture

- Unit tests use `respx` to mock httpx — no network at all.
- The single SIM-live integration test (`engine/tests/integration/test_saxo_live.py`)
  is the only test that hits the real `gateway.saxobank.com/sim`. It is
  gated by `SAXO_RUN_LIVE_TESTS=1` and excluded from `make test` and CI.
- Live SIM credentials expire after ~20 days — re-create the developer
  portal SIM account if `/users/me` starts returning auth errors.
