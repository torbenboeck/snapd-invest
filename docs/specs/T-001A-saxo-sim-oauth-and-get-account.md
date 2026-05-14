# T-001-A — Saxo SIM OAuth + `get_account`

**Status:** Design approved 2026-05-14
**Supersedes scope of:** First half of [`T-001`](../../tasks/T-001-saxo-sim-integration.md)
**Companion (deferred):** T-001-B — broker trading methods (place_order, cancel, get_positions, get_last_price, idempotency, MicroTrader wiring)

---

## 1. Context

MVP has only `PaperBroker`. Saxo SIM is the next execution venue. Before the engine can place SIM orders, it must:

1. Authenticate against Saxo's OAuth and obtain access + refresh tokens.
2. Persist the refresh token in a way that survives engine restarts.
3. Demonstrate that auth works against a real Saxo endpoint without placing any orders.

T-001-A delivers (1)–(3). T-001-B layers order placement on top. Splitting limits review/blast radius (~1500 lines if combined) and means each PR has a tight acceptance bar. When T-001-A merges, MicroTrader continues to run on `PaperBroker` while SIM auth is exercised manually via the new CLI commands.

The original `tasks/T-001-saxo-sim-integration.md` was drafted before OAuth research and incorrectly listed `client_credentials` as the grant type. Saxo does **not** support `client_credentials` for retail developers. T-001-A corrects this to **Authorization Code Grant with PKCE** (Saxo's recommendation for "Native applications", per RFC 7636).

## 2. Scope

### In scope

- Authorization Code + PKCE handshake against `https://sim.logonvalidation.net/{authorize,token}`.
- Refresh-token persistence (encrypted at rest) with proactive + reactive refresh.
- `Cipher` protocol + `FernetCipher` default implementation, keyed by `SNAPD_ENCRYPTION_KEY`.
- New CLI command `snapd-invest init-keys` to generate the master key once.
- `SaxoBroker` class implementing `IBroker`, with only `get_account()` wired end-to-end.
- Broker selection by `Account.account_type` (`paper` → `PaperBroker`, `sim` → `SaxoBroker`, `live` blocked).
- `Settings` extended with `SAXO_ENV`, `SAXO_CLIENT_ID`, `SAXO_REDIRECT_URI`, `SNAPD_ENCRYPTION_KEY`.
- Engine routes: `POST /v1/oauth/saxo/start` (state-changing), `GET /v1/oauth/saxo/callback` (browser redirect target), `GET /v1/oauth/saxo/status` (read).
- CLI commands: `snapd-invest auth saxo --account <id>` (opens browser), `snapd-invest get-account`.
- `broker.py` refactored into a `broker/` package: `__init__.py`, `paper.py`, `saxo.py`, `saxo_oauth.py`.
- Module-map and boundary-discipline rule updated for the `broker/` package.
- Unit tests for OAuth state machine, cipher, and broker `get_account`.
- One env-gated SIM-live test, marked `@pytest.mark.saxo_live`, skipped unless `SAXO_RUN_LIVE_TESTS=1`.
- ADR-005 added to `docs/architecture/decision-log.md` capturing the OAuth-flow choice.

### Deferred to T-001-B

- `place_order` (market + limit), `cancel`, `get_positions`, `get_last_price`.
- Idempotency-key handling on top of Saxo's `ExternalReference` field.
- Hooking `SaxoBroker` into the MicroTrader scheduled job.
- Promotion-gate evaluation beyond ADR-003's "paper always allowed".

### Hard-disabled in both PRs

- `SAXO_ENV=live`. Blocked by `.claude/hooks/pre_tool_bash.py` and by an explicit guard in `Settings` validation.

## 3. User actions (one-time)

1. Sign in at https://www.developer.saxo with the SIM developer account.
2. Create a new SIM application:
   - **App type:** Native application (the documented gotcha — do not pick Web).
   - **Grant type:** Authorization Code + PKCE.
   - **Trade-enabled:** No. T-001-A is read-only; T-001-B revisits.
   - **Redirect URI:** `http://localhost:8000/v1/oauth/saxo/callback` (exact match — Saxo is strict on trailing slashes).
3. Copy the resulting **app key** (it is the OAuth `client_id`). PKCE has no `client_secret`.
4. Add to `engine/.env` (gitignored):
   ```
   SAXO_ENV=sim
   SAXO_CLIENT_ID=<app key>
   SAXO_REDIRECT_URI=http://localhost:8000/v1/oauth/saxo/callback
   ```
5. After T-001-A merges, run `snapd-invest init-keys` once to generate `SNAPD_ENCRYPTION_KEY` into `.env`. The engine refuses to start without it.

## 4. Architecture

### 4.1 OAuth handshake (PKCE)

```
CLI                 Engine                       Browser            Saxo
 |                    |                             |                  |
 |-- auth saxo ------>|                             |                  |
 |                    |  POST /v1/oauth/saxo/start  |                  |
 |                    |  → generates state +        |                  |
 |                    |    PKCE verifier/challenge  |                  |
 |                    |  → persists oauth_state row |                  |
 |<-- url ----------- |                             |                  |
 |-- Process.Start -->|--- open browser --------->|                  |
 |                    |                             |-- /authorize -->|
 |                    |                             |<-- consent UI --|
 |                    |                             |-- user accepts->|
 |                    |<- GET /callback?code=...&state=...             |
 |                    |  → validates state          |                  |
 |                    |  → POST /token with         |                  |
 |                    |    code + verifier ------------------------> |
 |                    |<--- access + refresh + expires_in ---------- |
 |                    |  → Cipher.encrypt + persist                    |
 |                    |--- HTML "you can close this tab" -->          |
 |-- poll /status --->|                             |                  |
 |<-- ready --------- |                             |                  |
```

Key behaviors:

- `state` is the CSRF token **and** the account demux key. The engine generates it, stores it in `oauth_state` with `account_id`, and looks it up on the callback to identify which account the handshake is for.
- `code_verifier` is per-handshake, server-side, deleted after the token exchange (whether successful or not).
- Token refresh runs:
  - **Proactively** when `access_expires_at < now + 60s` (configurable buffer).
  - **Reactively** on a 401 response from Saxo, retrying the original call once.
- Refresh failure raises `BrokerAuthError`. The autonomous job logs the failure + skips the tick — no silent fail.
- `structlog` event hooks on the broker's `httpx.AsyncClient` redact `Authorization`, `access_token`, and `refresh_token` from every logged request/response.

### 4.2 Schema additions (Alembic migration)

```sql
CREATE TABLE oauth_state (
    id              TEXT     PRIMARY KEY,
    account_id      TEXT     NOT NULL REFERENCES accounts(id),
    broker          TEXT     NOT NULL,                -- 'saxo'
    state           TEXT     NOT NULL UNIQUE,
    code_verifier   TEXT     NOT NULL,
    created_at      DATETIME NOT NULL,                -- UTC, tz-aware
    expires_at      DATETIME NOT NULL                 -- created_at + 10 min
);
CREATE INDEX ix_oauth_state_account_id ON oauth_state(account_id);

CREATE TABLE oauth_tokens (
    id                       TEXT     PRIMARY KEY,
    account_id               TEXT     NOT NULL REFERENCES accounts(id),
    broker                   TEXT     NOT NULL,       -- 'saxo'
    access_token_encrypted   TEXT     NOT NULL,       -- Fernet ciphertext
    refresh_token_encrypted  TEXT     NOT NULL,       -- Fernet ciphertext
    access_expires_at        DATETIME NOT NULL,
    refresh_expires_at       DATETIME NOT NULL,
    created_at               DATETIME NOT NULL,
    updated_at               DATETIME NOT NULL,
    UNIQUE (account_id, broker)
);
```

Composite uniqueness on `(account_id, broker)` is the multi-user seam.

### 4.3 Code structure

```
engine/src/snapd_invest/
├── broker/
│   ├── __init__.py        # re-exports IBroker, OrderRequest, FillResult,
│   │                      #   PaperBroker, SaxoBroker, BrokerError + subclasses
│   ├── paper.py           # PaperBroker (moved verbatim from broker.py)
│   ├── saxo.py            # SaxoBroker (T-001-A: get_account only)
│   └── saxo_oauth.py      # PKCE state machine, token exchange, refresh, persistence
├── crypto.py              # Cipher protocol + FernetCipher
└── api.py                 # extended with /v1/oauth/saxo/{start,callback,status}
```

`broker.py` is replaced by the package; `from snapd_invest.broker import IBroker, PaperBroker` continues to work via the `__init__.py` re-exports. Module-map and the boundary-discipline rule (`broker.py` is the only Saxo importer) update to "the `broker/` package".

### 4.4 Error types (in `broker/__init__.py`)

```python
class BrokerError(Exception): ...
class BrokerAuthError(BrokerError): ...            # token problems
class BrokerHttpError(BrokerError):                # 4xx/5xx from Saxo
    status_code: int
    body: str
class BrokerTimeoutError(BrokerError): ...
```

All wrap the underlying `httpx` exception. An audit event is recorded at each broker call boundary (success or failure).

> **T-001-B foreshadowing:** Exceptions are the right tool for T-001-A's `get_account` (two meaningful outcomes — got it or didn't). T-001-B's `place_order` will have many meaningful outcomes the caller has to discriminate (`Filled`, `PartiallyFilled`, `Rejected(reason)`, `BrokerDown`, `IdempotentReplay(original_id)`, etc.) — those land as a typed discriminated union (`Literal`-tagged dataclasses + `match` + `assert_never`), not exceptions. Two error-handling shapes coexisting in one module is intentional; each fits its surface.

### 4.5 `Cipher` abstraction

```python
class Cipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...

class FernetCipher:
    def __init__(self, key: bytes) -> None: ...
    # uses cryptography.fernet.Fernet
```

Single-tenant today: one `FernetCipher` instance constructed from `SNAPD_ENCRYPTION_KEY`, injected into the `oauth_tokens` service functions.

Multi-tenant tomorrow: a `KeyProvider` adds a layer — `FernetCipher(KmsKeyProvider(account_id).fetch())` — without schema or service-function changes.

`init-keys` CLI command generates a key via `Fernet.generate_key()`, writes it to `.env`, refuses to overwrite if one already exists.

### 4.6 Multi-user readiness summary

- All new tables scope by `account_id` from day one.
- `Cipher` abstracts the key source; key rotation and per-tenant keys land later without schema change.
- One redirect URI serves N accounts (account demuxed via `state`).
- `SAXO_CLIENT_ID` stays in env (Saxo OAuth app is per-project, not per-user). Per-user config goes in DB.

## 5. Test strategy

| Test file | Coverage focus |
|---|---|
| `tests/unit/test_cipher.py` | Fernet roundtrip; missing-key raises clearly; tampered-ciphertext raises. **Target: 100%.** |
| `tests/unit/test_saxo_oauth.py` | PKCE verifier/challenge generation; `state` validation; expired `oauth_state` rejection; token exchange happy path; refresh-on-401; refresh failure raises `BrokerAuthError`. |
| `tests/unit/test_saxo_broker.py` | `get_account` happy path against respx-mocked `/port/v1/accounts/me`; `BrokerAuthError` wrapping on 401 + refresh failure; `BrokerHttpError` wrapping on other 4xx/5xx. |
| `tests/integration/test_saxo_live.py` | One end-to-end `get_account()` call against real SIM. Asserts a parseable response. Marked `@pytest.mark.saxo_live`. Skipped unless `SAXO_RUN_LIVE_TESTS=1`. |

- Coverage targets: `broker/saxo*` and `broker/saxo_oauth.py` ≥ 90%. `crypto.py` 100%.
- `@pytest.mark.saxo_live` registered in `pyproject.toml`'s `markers` table.
- `AGENTS.md` updated with a "Running SIM-live tests" subsection that documents the `SAXO_RUN_LIVE_TESTS=1` opt-in and explicitly states it is excluded from `make test` and from CI.

## 6. Acceptance criteria

- [ ] PKCE handshake completes end-to-end against `https://sim.logonvalidation.net` (verified by the env-gated live test).
- [ ] Tokens persist in `oauth_tokens` (encrypted) across engine restarts.
- [ ] Proactive refresh fires when `access_expires_at < now + 60s`.
- [ ] Reactive refresh fires on a 401 and retries the original call exactly once.
- [ ] `snapd-invest auth saxo` opens the browser, completes consent, returns "tokens stored".
- [ ] `snapd-invest get-account` prints the account ID and cash from real SIM.
- [ ] Engine refuses to start if `SNAPD_ENCRYPTION_KEY` is missing or malformed.
- [ ] `SAXO_ENV=live` is hard-blocked by `Settings` validation.
- [ ] All unit tests pass; coverage targets met.
- [ ] `make test`, `make lint`, `cd engine && uv run mypy src` all clean.
- [ ] ADR-005 appended.
- [ ] Module-map updated for the `broker/` package.

## 7. Open questions

None blocking. Saxo's actual token TTLs (access vs refresh) will be observed from the first SIM exchange and documented in ADR-005 once known.

## 8. Verify

```bash
make test
make lint
cd engine && uv run mypy src

# Optional, requires SAXO_* in .env and explicit opt-in:
SAXO_RUN_LIVE_TESTS=1 uv run pytest tests/integration/test_saxo_live.py -v
```
