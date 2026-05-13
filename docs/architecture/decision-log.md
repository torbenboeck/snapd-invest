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
