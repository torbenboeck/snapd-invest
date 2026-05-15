# Architecture Decision Log

Append-only log of architectural decisions for snapd-invest.
Each entry follows ADR-lite format: context, decision, consequences.

---

## ADR-001 — Hybrid Python + .NET stack

**Date:** 2026-05-12
**Status:** Accepted

### Context

snapd-invest needs to combine:

- A fast-iterating quantitative trading engine (market data, indicators, strategies, backtesting, LLM-driven analyst agents)
- A polished user-facing client (CLI initially, web/mobile later)

The user's default stack is .NET 10 (extensive experience in .NET, Azure, DDD). The quantitative finance and ML/LLM tooling ecosystem in Python is materially more mature than in .NET (pandas, polars, vectorbt, backtrader, NautilusTrader, scikit-learn, yfinance, ccxt, etc.).

### Options considered

1. **Pure .NET** — build everything in .NET, including backtester, indicators, broker adapters, LLM integration. Pro: stack consistency. Con: 3–6 months of custom code reproducing Python ecosystem functionality.
2. **Pure Python** — abandon the .NET preference entirely. Pro: maximum velocity. Con: forfeits the user's strongest skillset; harder to extend with web/mobile clients later.
3. **Hybrid: Python engine + .NET client** — Python owns trading logic and persistence; .NET owns user-facing client; they communicate over HTTP.

### Decision

**Option 3: Hybrid Python engine + .NET client.**

- Python service (`engine/`) exposes a FastAPI HTTP interface.
- .NET CLI (`cli/`) talks to the engine via a typed HTTP client.
- The engine owns all persistence at MVP. The CLI is stateless.
- Communication is HTTP/JSON. May upgrade to gRPC if latency becomes a problem (not anticipated).
- Contract drift detection: NSwag generates the .NET client from the FastAPI OpenAPI spec on every CLI build.

### Consequences

**Pro:**
- Direct access to Python's mature quant and ML ecosystem.
- .NET retains its role in user-facing UX (and future web/mobile via Blazor/MAUI).
- Clear contract boundary forces clean API design from day 1.
- Each stack is independently testable.

**Con:**
- Two runtimes locally. Two package managers (uv, dotnet). Two test runners. Two CI pipelines.
- Cross-stack changes require coordination on the OpenAPI contract.
- Single-developer cognitive load is non-trivial early on.

### Notes

The user explicitly accepted the two-runtime cost in exchange for ecosystem access. Revisit this decision if maintenance burden becomes painful — pulling the engine into a .NET implementation later is feasible because we keep the API surface narrow.

---

## ADR-002 — Ultra-light architecture (no DDD ceremony)

**Date:** 2026-05-12
**Status:** Accepted

### Context

The user has deep DDD experience but explicitly wants snapd-invest to be "ultra-light" with consistent terminology rather than DDD ceremony. The MVP is single-user and local.

### Decision

- **Strategic DDD: on.** Ubiquitous language is mandatory and lives in `docs/ubiquitous-language.md`. Term consistency across code, API, database, and docs is enforced in review.
- **Tactical DDD: off by default.** No aggregates, repositories, strongly typed IDs, domain events, or CQRS unless a concrete trigger justifies introducing one.
- **Module structure:** flat Python packages, not nested per "bounded context". Each module is one file at first; split when it grows.
- **Abstractions only where they earn their keep:**
  - `IBroker` protocol — required for swapping execution venues and unit testing.
  - `Clock` protocol — required for deterministic time in tests.
  - `ILlmProvider` protocol — required for swapping LLMs and avoiding live LLM calls in CI.

### Consequences

- Lower cognitive load early.
- Easy for Claude Code to navigate (low indirection).
- Need to revisit if a module grows past ~500 lines or if a bounded context emerges naturally.

---

## ADR-003 — Promotion gates encoded in code, not custom

**Date:** 2026-05-12
**Status:** Accepted

### Context

User principles require: backtesting before paper-with-stakes, paper-with-stakes before live. The user wants this enforced by the system, not by discipline.

### Decision

- Each strategy declares a promotion configuration (which evals must pass to enter `paper`, `sim`, `live`).
- The engine refuses to bind a strategy to an environment whose gate has not been met.
- For MVP (paper-only, internal): the gate is a constant `paper: always_allowed` so we can iterate without ceremony.
- Real promotion gates with eval thresholds are introduced when Saxo SIM integration arrives (likely week 4+).

### Consequences

- The principle is real even when the gate is trivial — the structure is in place.
- Adding rigor later is a config change, not a refactor.

---

## ADR-004 — Single SQLite DB, owned by Python engine

**Date:** 2026-05-12
**Status:** Accepted

### Context

Two stacks (Python and .NET) could each have their own database, but that introduces dual-write problems and complicates auditing.

### Decision

- The Python engine owns a single SQLite database at `engine/data/snapd_invest.db`.
- The .NET CLI does not persist state at MVP. All reads/writes go through the engine HTTP API.
- Schema is migration-friendly to PostgreSQL via Alembic. Avoid SQLite-only features.
- Monetary values use `DECIMAL` with explicit precision (18, 4).
- All timestamps are UTC.

### Consequences

- Single source of truth.
- Easy backup (copy one file).
- No multi-process write contention at MVP (FastAPI + APScheduler share one process and one connection pool).
- If we later need .NET-side persistence (user config, multi-tenant), introduce a separate DB then.

---

## ADR-005 — Saxo OAuth: Authorization Code + PKCE

**Date:** 2026-05-14
**Status:** Accepted

### Context

T-001-A wires Saxo SIM as the second execution venue. Saxo OpenAPI exposes four OAuth 2.0 flows: Authorization Code Grant, Authorization Code Grant + PKCE, Implicit Flow, Certificate-Based Authentication. The original `tasks/T-001-saxo-sim-integration.md` named `client_credentials`. That flow is not supported by Saxo for retail developers; the task spec was drafted before OAuth research.

### Options considered

1. **Authorization Code Grant** — server-side web app. Requires `client_secret`. Works against localhost callbacks.
2. **Authorization Code Grant + PKCE** — native / desktop app. No `client_secret`; PKCE verifier replaces it. Works against localhost callbacks. Saxo's docs explicitly endorse this for "Native applications" (RFC 7636).
3. **Implicit Flow** — single-page app. No refresh token. Disqualifying for autonomous MicroTrader.
4. **Certificate-Based Authentication** — "select partners upon request" only.

### Decision

**Authorization Code + PKCE**, with the engine acting as a "Native application" registered against the user's SIM developer account.

- No `client_secret` in `.env` — PKCE removes that attack surface.
- One-time browser-based consent at first run; refresh token persists for subsequent runs.
- `state` parameter doubles as account demux (multi-user readiness) — one redirect URI serves N accounts.
- Tokens encrypted at rest in `oauth_tokens` table via a `Cipher` abstraction keyed by `SNAPDINVEST_ENCRYPTION_KEY`.

### Consequences

**Pro:**
- Smaller secrets footprint (no client_secret).
- Saxo's native-app registration is the documented happy path; less likely to hit portal-side gotchas.
- PKCE verifier per-handshake means a leaked authorization code is useless without the corresponding verifier.

**Con:**
- Requires running a local HTTP listener (the engine) to receive the callback. Acceptable — the engine already listens.
- The Saxo developer portal defaults new apps to "Web application"; the user must select "Native application" at registration (documented gotcha).

### Notes

- SIM endpoints: `https://sim.logonvalidation.net/{authorize,token}`.
- SIM API base: `https://gateway.saxobank.com/sim/openapi/`.
- Live endpoints are explicitly NOT configured; `SNAPDINVEST_SAXO_ENV=live` is blocked by `Settings` validation and by `.claude/hooks/pre_tool_bash.py`.
- Token TTLs (access vs refresh) will be observed at first SIM exchange and appended here.
- **Redirect URL has an unusual rule:** registered in the Saxo portal *without* a port (`http://localhost/v1/oauth/saxo/callback`), but **sent** to `/authorize` *with* the port (`http://localhost:8000/v1/oauth/saxo/callback`). Saxo's auth server matches scheme+host+path and ignores the port. Confirmed against Saxo's official C# PKCE sample. See [`docs/integrations/saxo-openapi-notes.md`](../integrations/saxo-openapi-notes.md) for the full story.

---

## How to add an entry

Append a new ADR section using the next number. Format:

```
## ADR-NNN — Short title

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Superseded by ADR-NNN | Deprecated

### Context
What is the situation? What is the problem?

### Decision
What did we decide?

### Consequences
What follows from this — positive and negative?
```

Do not edit past ADRs after they are accepted. If a decision is reversed, add a new ADR that supersedes the old one.
