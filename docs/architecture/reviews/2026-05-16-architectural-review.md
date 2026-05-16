# snapd-invest — Architectural Review

> Snapshot date: 2026-05-16
> Branch reviewed: `docs/T-001-B-spec-and-plan`
> Reviewer: Claude (Opus 4.7), synthesized from 5 parallel exploration agents
> Audience: a follow-up Claude Code agent who will pick up unresolved findings

This is an architectural-quality review of the snapd-invest repository as it stands today, intended to brief a downstream Claude Code session with full context. It assumes you have not read the codebase; every claim is anchored to a `file:line` or commit reference you can verify.

---

## 0. TL;DR

snapd-invest is a hybrid Python (FastAPI) + .NET 10 (Spectre.Console) agentic trading platform in pre-MVP, single-user, paper-only state. The scaffold is in good shape: clean layering, strong principle compliance, well-paired spec/plan/task artifacts, and a defense-in-depth harness. Several items need attention before T-001-B (Saxo SIM trading) lands.

**Highest-value findings:**

| # | Severity | Finding | Fix size |
|---|---|---|---|
| F-01 | **HIGH** | `tasks/_next.md:6` points at the superseded `T-001-saxo-sim-integration.md`; the actual next task is `T-001-B`. Any agent invoking `/next-task` will get confused. | 5-line doc edit |
| F-02 | **MEDIUM** | `engine/src/snapd_invest/api.py:640,760` execute `select(Account)` directly inside route handlers — minor breach of the "Route → service → persistence" rule. Extract a `portfolio.get_account_by_id()` helper for symmetry with `get_account_by_name()`. | 1 helper + 2 callsites |
| F-03 | **MEDIUM** | Risk gate declares `max_daily_loss_pct` (`engine/src/snapd_invest/risk.py:37`) but no code checks it. The principle "Risk gate is always on" is only half-true. | New check + tests |
| F-04 | **MEDIUM** | T-001-A shipped without a `tasks/T-001-A-*.md` file (only spec + plan + PR #5). This breaks the implicit invariant that every task has a queue file. Either backfill the file (marked `done`) or document the spec-and-plan-only path in `tasks/README.md`. | doc choice |
| F-05 | **MEDIUM** | `tasks/T-001-saxo-sim-integration.md` is `Status: superseded` but still sits at the top of `_next.md`'s backlog. Archive it (`tasks/.archive/`) or rename to `T-001-DEPRECATED-*.md`. | rename + history note |
| F-06 | **MEDIUM** | `README.md:35` marks `docs/setup.md` as TODO, but the file exists and is usable. Drop the marker. README also never links to `AGENTS.md`. | 2-line README edit |
| F-07 | **MEDIUM** | `docs/architecture/module-map.md` is missing `pipeline.py` (introduced by T-003, now merged). | 1 table row |
| F-08 | **LOW–MEDIUM** | `cli/src/SnapdInvest.Cli/Program.cs` (HTTP-client config) does not honor the `--engine-url` CLI flag that `cli/CLAUDE.md` documents as highest-priority. Doc/impl drift. | Either implement or correct the doc |
| F-09 | **LOW–MEDIUM** | Untyped `Dictionary<string, object?>` "outcomes" in `ApproveCommand` and `RunOnceCommand` are addressed by stringly-typed `TryGetValue` lookups (e.g. `"gate_allowed"`, `"order_status"`). Will silently break on engine schema rename. T-005 (NSwag) is the planned fix; until then it's a latent contract bomb. | Wait for T-005 |
| F-10 | **LOW** | `.claude/settings.local.json` exists locally with personal GitHub PATs in it. Status is **safe** (file is `.gitignored` at `.gitignore:102` and was never committed with PAT content — verified via `git log -S"github_pat_"`). Mention here only to correct a misleading "CRITICAL" framing that was flagged during this review and dismissed on verification. | None — confirm rotation cadence is sane |

Everything in this report is anchored further below.

---

## 1. What this system is

**Vision** (`README.md:8-10`): democratize informed investing for non-experts by offering multiple agent "personalities" that propose trades — alongside a deterministic micro-trader running rule-based intraday strategies. Single-user MVP, local-only, paper-only.

**Two execution paths**:

- **MicroTrader** — deterministic, rule-based (`engine/src/snapd_invest/strategy.py`). Runs autonomously within hard risk limits.
- **Agentic agents** — LLM-driven analysts with `Personality` bundles (`engine/src/snapd_invest/agent.py:48-73`). They produce `Recommendation`s; a human approves before execution.

**Three execution venues**, gated by promotion: internal paper broker → Saxo SIM → Saxo live. Live is hard-blocked in `engine/src/snapd_invest/config.py:122-128` (`Settings` validator raises `ValueError` if `saxo_env=='live'`).

**Pipeline shape**: market data → signal → risk gate → order management → execution. Each step isolated and testable.

**Five canonical terms** (`docs/ubiquitous-language.md`): Signal, Recommendation, Order, Trade, Position.

---

## 2. Repository topology

```
snapd-invest/
├── engine/             Python 3.12+ service (FastAPI). Owns trading logic, agents, persistence, broker adapters.
├── cli/                .NET 10 client (Spectre.Console). UX only; talks to engine via HTTP.
├── docs/               Specs, plans, ADRs, language, integration notes, MVP scope.
├── tasks/              Ralph-loop queue (T-001..T-005 + _next.md + _template.md).
├── .claude/            Harness: settings.json, settings.local.json (gitignored), hooks, commands, skills.
├── scripts/git-hooks/  Repo-tracked pre-commit hook (blocks direct commits to main/develop/release/*).
├── .github/workflows/  engine-ci.yml, cli-ci.yml.
├── AGENTS.md           Operating manual for Claude Code in this repo.
├── CLAUDE.md           Root guidance (principles, ownership, forbidden patterns).
├── HANDOVER.md         Initial scaffold handover (2026-05-12).
├── Directory.Build.props  .NET solution-wide settings.
├── Makefile            Cross-stack commands.
└── README.md           Entry point.
```

`engine/CLAUDE.md` and `cli/CLAUDE.md` are loaded automatically when Claude works in those subdirectories.

**Ownership boundary, from `CLAUDE.md`**: "if it touches money, it's Python. If it's about what the user sees, it's .NET." Saxo calls live exclusively in Python. The .NET CLI is presentation-only.

---

## 3. Engine architecture (Python)

### 3.1 Module layout

Flat package at `engine/src/snapd_invest/`. One file per concept. No subpackages except `broker/` (split out during T-001-A). 24 modules + a `tools/` helper.

| Module | Responsibility | Key types |
|---|---|---|
| `api.py` | FastAPI routes + middleware + lifespan | `create_app()`, ~14 endpoints under `/v1/*` |
| `audit.py` | Append-only audit event recording + queries | `record_event`, `list_events` |
| `agent.py` | LLM analyst agents; prompt building; recommendation generation | `Personality`, `CONSERVATIVE_VALUE`, `MOMENTUM_TRADER`, `run_agent` |
| `broker/__init__.py` | Broker protocol + DTOs + errors + factory | `IBroker`, `OrderRequest`, `FillResult`, `BrokerError`, `BrokerFactory` |
| `broker/paper.py` | In-memory paper broker | `PaperBroker` |
| `broker/saxo.py` | Saxo SIM broker (T-001-A scope: `get_account` only) | `SaxoBroker` |
| `broker/saxo_oauth.py` | Saxo OAuth PKCE + token lifecycle | `generate_pkce`, `exchange_code_for_tokens`, `get_active_access_token` |
| `clock.py` | Injected time source | `Clock` (Protocol), `SystemClock`, `FakeClock` |
| `config.py` | Env-driven Pydantic Settings; live trading hard-block | `Settings`, `get_settings` |
| `crypto.py` | Fernet symmetric encryption for OAuth tokens | `Cipher` (Protocol), `FernetCipher` |
| `data.py` | Market data fetching, caching, bar queries | `BarData`, `IMarketDataProvider`, `FakeMarketDataProvider` |
| `execution.py` | Signal → Risk Gate → Order pipeline | `execute_signal`, `execute_signals`, `ExecutionOutcome` |
| `indicators.py` | Pure math (SMA, EMA, crossover) | `sma`, `ema`, `crossover` |
| `llm.py` | LLM provider abstraction | `ILlmProvider` (Protocol), `OllamaProvider`, `FakeLlmProvider` |
| `logging_config.py` | structlog setup | `configure_logging`, `get_logger` |
| `models.py` | SQLAlchemy ORM models (single schema source) | `AuditEvent`, `Instrument`, `Bar`, `Account`, `Position`, `Order`, `Trade`, `Agent`, `Recommendation`, `OAuthState`, `OAuthToken`, `new_id` |
| `persistence.py` | Async engine + session factory | `make_engine`, `make_session_factory`, `session_scope` |
| `pipeline.py` | Per-tick orchestration (MicroTrader + Agent + expiry) | `run_microtrader_once`, `run_agent_once`, `expire_overdue_recommendations` |
| `portfolio.py` | Portfolio queries + P&L | `create_account`, `get_account_by_name`, `list_positions`, `build_summary` |
| `recommendation.py` | Recommendation lifecycle | `create_recommendation`, `approve_and_execute`, `reject`, `expire_overdue` |
| `risk.py` | Risk gate (position sizing, allowlist, kill switch) | `RiskConfig`, `RiskDecision`, `evaluate` |
| `scheduler.py` | APScheduler integration | `build_scheduler`, `build_default_jobs` |
| `strategy.py` | Deterministic strategies (SMA Crossover only) | `Signal`, `IStrategy`, `SMACrossoverStrategy` |
| `tools/init_keys.py` | One-shot encryption key generator | `run`, `KeyAlreadyExistsError` |

### 3.2 Layering — clean with two minor leaks

- ✅ Routes call service functions (`portfolio.get_account_by_name`, `recommendation.approve_and_execute`), not raw queries.
- ✅ Domain types (`Signal`, `BarData`, `RiskDecision`) are dataclasses, decoupled from ORM models.
- ⚠️ `api.py:640` (`/v1/oauth/saxo/start`) and `api.py:760` (`/v1/accounts/{account_id}`) both `session.execute(select(Account)...)` directly. Both are shallow lookups for broker-factory dispatch; both deserve a `portfolio.get_account_by_id(session, id)` helper for symmetry. (Finding F-02.)

### 3.3 Hard-rule compliance scorecard

| Rule | Status | Evidence |
|---|---|---|
| No `datetime.utcnow()` / `datetime.now()` in domain code | ✅ | Only `clock.py:26` (SystemClock impl) and `test_saxo_live.py:52` (live-test only). All domain code uses injected `Clock`. |
| No `uuid.uuid4()` in business logic | ✅ | Centralized in `models.py:40` (`new_id`). Other call sites are middleware/boundary. |
| No `print()` in production | ✅ | Zero matches under `src/`. structlog everywhere. |
| Risk gate always runs before order placement | ✅ | `execution.py:65-166` is the only path to `broker.place_order`; gate evaluated at line 101. |
| Live broker hard-blocked | ✅ | `config.py:122-128` raises `ValueError` if `saxo_env=='live'`. |
| No live broker in tests | ✅ | `PaperBroker` everywhere; `test_saxo_live.py` gated behind `@pytest.mark.saxo_live` + `SAXO_RUN_LIVE_TESTS=1`. |
| No live LLM in CI | ✅ | `FakeLlmProvider` in tests; Ollama not invoked. |
| No direct DB access from FastAPI handlers | ⚠️ | 2 leaks: `api.py:640, 760` (see F-02). |
| Daily-loss check | ❌ | Declared in `RiskConfig` (`risk.py:37`) but never evaluated (F-03). |
| Timezone-aware UTC everywhere | ✅ | All ORM columns are `DateTime(timezone=True)`. `_as_utc` helper in `saxo_oauth.py:41-48` handles SQLite naive datetimes. |

### 3.4 Risk gate

`risk.py:70-114` — `evaluate(SignalCandidate)` enforces:
1. Kill switch (line 76)
2. Positive quantity (line 79)
3. Instrument allowlist if configured (line 83-86)
4. Reference price required for buys (line 90)
5. Cash sufficiency for buys (line 98-103)
6. Order value ≤ equity × `max_position_pct_of_equity` (default 20%) (line 105-112)

`max_daily_loss_pct` is declared (line 37, default 5%) but **never checked**. F-03.

### 3.5 Persistence — Alembic + SQLAlchemy on SQLite

5 migrations, monotonically dated:

1. `0001_initial_schema` — Audit, Instrument, Bar
2. `0002_trading_schema` — Account, Position, Order, Trade
3. `0003_agent_schema` — Agent, Recommendation
4. `0004_oauth_schema` — OAuthState, OAuthToken (T-001-A)
5. `0005_account_saxo_identity` — `saxo_client_key`, `saxo_account_key`, `saxo_account_id` columns

Audit trail: append-only `audit_events` with `(type, occurred_at)` index and `correlation_id` for request tracing — generated in middleware (`api.py:226`) and propagated to logs and DB.

Idempotency: `Order.idempotency_key` is unique; `PaperBroker` checks for replay (`paper.py:38-42`).

### 3.6 Broker adapters

`IBroker` protocol (`broker/__init__.py:88-91`):
```python
async def place_order(session, request) -> FillResult
async def get_last_price(session, instrument) -> Decimal | None
```

- `PaperBroker` — in-memory, market orders fill at last bar close, limits validated, idempotent.
- `SaxoBroker` — currently only `get_account()` (T-001-A scope). `place_order` etc. land in T-001-B per `docs/specs/T-001B-saxo-trading.md`.
- `BrokerFactory` (`broker/__init__.py:100`) maps `Account` to broker, dispatched in `api.py:134-136`. Live path raises.

### 3.7 Agent lifecycle

Recommendation states (`recommendation.py`): `pending → approved | modified | rejected | expired | executed`.

- `create_recommendation` — wraps signals with TTL (default 24h, `recommendation.py:39`).
- `approve_and_execute` — runs the same `execute_signals` pipeline as MicroTrader, so risk gate also enforced for human-approved trades.
- `expire_overdue` — sweep job marks stale recs as expired.

Two pre-built personalities (`agent.py`): `CONSERVATIVE_VALUE`, `MOMENTUM_TRADER`. Output is a typed JSON schema (`AGENT_OUTPUT_SCHEMA`), validated against personality config (min conviction, watchlist membership).

### 3.8 Scheduler — T-003 fully wired

`api.py:138-150` confirms APScheduler is built, default jobs registered, started in lifespan, shutdown on app exit. T-003 is **merged and complete** (commit `fc27f06`, task file says `Status: done`).

### 3.9 Testing

- ~80 unit tests under `engine/tests/unit/`, one file per module.
- One gated integration test `engine/tests/integration/test_saxo_live.py` (`@pytest.mark.saxo_live`, skipped unless `SAXO_RUN_LIVE_TESTS=1`).
- Fakes: `FakeClock`, `FakeLlmProvider`, `FakeMarketDataProvider`, `PaperBroker`.
- No end-to-end test yet (deliberate — that's T-004).

### 3.10 Smells worth surfacing

- N+1 in `portfolio.build_summary` (one instrument query per position). Not a problem at MVP scale; flag at 100+ positions.
- Stale `OAuthState` rows accumulate; no background cleanup job.
- `OllamaProvider` model hardcoded to `llama3.1` (`llm.py:56`). User memory says this is an open question and Ollama/open-source must remain the default.
- Result-type approach is informal (`ValueError` raised from service functions for business errors). `engine/CLAUDE.md` flags this as an open design question. Acceptable for MVP.

---

## 4. CLI architecture (.NET)

### 4.1 Solution map

`SnapdInvest.sln` has three projects under `cli/`:

- **`SnapdInvest.Cli`** (Exe, net10.0) — Spectre.Console host. References Refit, Serilog, Microsoft.Extensions.*.
- **`SnapdInvest.Client`** (Library, net10.0) — Refit interface + DTOs. Zero business logic.
- **`SnapdInvest.Cli.Tests.Unit`** (xUnit + Shouldly + NSubstitute).

Central package management via `cli/Directory.Packages.props`; analyzers + `TreatWarningsAsErrors=true` enforced through `Directory.Build.props` at repo root.

### 4.2 Command surface

10 Spectre.Console commands wired in `Program.cs:62-99`:

| Command | Settings | Engine endpoint(s) |
|---|---|---|
| `status` | `--account` | `GET /v1/portfolio`, `GET /v1/recommendations` |
| `run-once` | `--symbol`, `--exchange` | `POST /v1/run-once` |
| `run-agent` | `--symbol`, `--exchange` | `POST /v1/agents/run` |
| `audit` | `--limit`, `--type` | `GET /v1/audit` |
| `recos` | `--status`, `--limit` | `GET /v1/recommendations` |
| `approve <id>` | `--yes`, `--modify` (repeatable) | `POST /v1/recommendations/{id}/approve` |
| `reject <id>` | `--reason` | `POST /v1/recommendations/{id}/reject` |
| `auth saxo` | `--account`, `--poll-interval-ms`, `--max-attempts` | `POST /v1/oauth/saxo/start`, `GET /v1/oauth/saxo/status` |
| `get-account` | `--account` | `GET /v1/accounts/{id}` |
| `create-account` | many | `POST /v1/accounts` |

### 4.3 HTTP contract

`IEngineApi` (in `SnapdInvest.Client/IEngineApi.cs`) is a hand-written Refit interface, 13 endpoints. JSON serializer config (`Program.cs:42-48`):

```csharp
PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower,
NumberHandling = JsonNumberHandling.AllowReadingFromString,
```

This handles two engine idiosyncrasies — snake_case fields and decimals serialized as strings (Pydantic V2). PR #7 introduced this; 10 round-trip tests verify it in `JsonSerializationTests`.

DTOs are sealed records, positional, with no `JsonPropertyName` attributes (relies on global policy).

### 4.4 Hard-rule compliance — CLI

| Rule | Status | Evidence |
|---|---|---|
| No `DateTime.UtcNow` / `DateTime.Now` in src | ✅ | All timestamps come from engine as strings. |
| No `Guid.NewGuid()` in business logic | ✅ | IDs sourced from engine. |
| No `Console.WriteLine` / raw stdout | ✅ | Spectre AnsiConsole only. |
| No direct Saxo / broker calls | ✅ | All HTTP via engine. |
| No business logic on CLI side | ✅ | Commands render outcomes, don't compute them. |

### 4.5 Doc/impl drift — `--engine-url`

`cli/CLAUDE.md` documents `--engine-url` as highest-priority config override, but `Program.cs:52-57` only reads `Engine:Url` from configuration (which means `appsettings.json` or `SNAPDINVEST_ENGINE__URL` env var). The CLI flag is not implemented. Either implement it (one `services.Configure<EngineOptions>(...)` extension + a top-level option) or fix the doc. (Finding F-08.)

### 4.6 Latent contract holes

`ApproveCommand` and `RunOnceCommand` parse `Dictionary<string, object?>` outcomes via `TryGetValue("gate_allowed")`, `TryGetValue("order_status")`, etc. Misspelled keys silently produce `?` in the rendered output. If the engine renames a key, the CLI doesn't fail — it just stops showing useful information. T-005 (NSwag) is the planned permanent fix. (Finding F-09.)

### 4.7 Other CLI observations

- `ErrorOr` NuGet is referenced in `Directory.Packages.props` but unused. Dead dependency.
- `Serilog` is configured (`appsettings.json:5-10`) but no command actually calls `ILogger` — logging is idle.
- 22 tests total (10 JSON round-trip + 12 command-specific). 5 of 10 commands have no tests at all (`run-once`, `run-agent`, `audit`, `recos`, `reject`).
- Correlation IDs come back from the engine (`AuditEventDto.correlation_id`) but the CLI doesn't propagate them across subsequent requests.

---

## 5. Documentation system

### 5.1 Map

| Path | Purpose | Freshness |
|---|---|---|
| `README.md` | Entry, vision, layout | Stale: `setup.md` marked TODO, missing AGENTS.md link (F-06) |
| `CLAUDE.md` | Root guidance (principles, ownership, forbidden patterns) | Current |
| `AGENTS.md` | Operating manual for Claude Code | Current |
| `HANDOVER.md` | One-shot scaffold handover | Current; not linked from README |
| `engine/CLAUDE.md` | Per-stack guidance | Current |
| `cli/CLAUDE.md` | Per-stack guidance | One drift (F-08) |
| `docs/ubiquitous-language.md` | Glossary | Missing a few terms (see 5.4) |
| `docs/product/mvp-scope.md` | In/out of MVP | Current |
| `docs/setup.md` | First-time + daily setup | Usable; README falsely says TODO |
| `docs/architecture/decision-log.md` | ADR-001 to ADR-005 | Current; ADR-006 expected with T-001-B |
| `docs/architecture/module-map.md` | Module ownership | Missing `pipeline.py` (F-07) |
| `docs/integrations/saxo-openapi-notes.md` | Saxo OpenAPI gotchas (PKCE asymmetry etc.) | Current; "Lessons learned" section pending T-001-B impl |
| `docs/specs/T-001A-*` + `docs/plans/2026-05-14-T-001A-*` | T-001-A pair | Done; matches PR #5 |
| `docs/specs/T-001B-*` + `docs/plans/2026-05-16-T-001-B-*` | T-001-B pair | Status `Proposed`; on the current branch |
| `docs/specs/T-003-*` + `docs/plans/2026-05-13-T-003-*` | T-003 pair | Done |

### 5.2 ADRs — five accepted, one expected

| ADR | Title | Status | Date |
|---|---|---|---|
| ADR-001 | Hybrid Python + .NET stack | Accepted | 2026-05-12 |
| ADR-002 | Ultra-light architecture (no DDD ceremony) | Accepted | 2026-05-12 |
| ADR-003 | Promotion gates encoded in code | Accepted | 2026-05-12 |
| ADR-004 | Single SQLite DB, owned by Python engine | Accepted | 2026-05-12 |
| ADR-005 | Saxo OAuth: Authorization Code + PKCE (native app) | Accepted | 2026-05-14 |

ADR-006 (OrderResult discriminated union + typed PromotionDecision) is locked into T-001-B plan Task 1 but not yet appended. Make sure the T-001-B PR includes it. The pipeline.py addition to module-map (F-07) and the saxo-openapi-notes "Lessons learned" section are similar "land-with-the-feature" follow-ups.

### 5.3 MVP scope vs current work

`docs/product/mvp-scope.md` explicitly says "No Saxo integration" is out of MVP — yet T-001-A and T-001-B are in flight. This is **not scope creep**: HANDOVER.md positions Saxo as the immediate unblocker after the bootstrap. The MVP "done definition" (8 user actions in `mvp-scope.md`) is unchanged.

### 5.4 Glossary gaps

`ubiquitous-language.md` defines ~35 terms but the following are used in specs and code without entries:

- **promotion gate** (used in CLAUDE.md, ADR-003, ADR-005, T-001-B spec §4.6)
- **eval status** ("passing"/"failing")
- **watchlist** (used in T-003 spec/plan; structure not documented)
- **recommendation state machine** (states listed, but state-transition semantics — e.g. how `modified` differs from `approved` — are not)
- **personality instances** (the file describes the *shape*, not the catalog `CONSERVATIVE_VALUE`, `MOMENTUM_TRADER`)

Pick one of: extend `ubiquitous-language.md`, or add cross-links from terms to the specs that define them. Either is fine; today's state has neither.

### 5.5 Setup doc

`docs/setup.md` is usable: prerequisites, one-time setup, daily run, configuration, verification, troubleshooting. Gaps are minor:
- `.env` generation isn't explicit ("copy `engine/.env.example`...")
- Ollama is "optional" but no setup steps if the user wants it
- Multi-machine guidance absent (acceptable for MVP)

Remove the `(TODO)` marker in `README.md:35`.

---

## 6. Claude Code harness

### 6.1 `.claude/settings.json`

**Permissions:**
- **Allow** — only `WebSearch`, `WebFetch` pre-approved.
- **Deny** — force-push variants, `gh repo delete`, repo-visibility flip, `git filter-branch`, `git update-ref -d`, `git reset --hard origin/{main,master}`, branch deletion (`-D`/`-d`) on `main|master|develop`.
- **Ask** — any `git push`, any branch deletion, `gh pr merge/close/create`, `rm -rf`, `alembic downgrade`, `dotnet ef database drop`.

**Hook**: `PreToolUse` on `Bash` → `python .claude/hooks/pre_tool_bash.py`. Exit codes: 0 allow, 1 silent block, 2 block + "revise approach" signal.

### 6.2 `.claude/hooks/pre_tool_bash.py`

Three validations:

1. **Branch-deletion safety** (lines 44-77) — only `feature/*` and `bugfix/*` may be deleted, and only after `git branch --merged main` confirms the merge. Exit 2 if not merged. Subprocess timeouts handled.
2. **Secrets safety** (lines 79-86) — blocks `cat|less|more|head|tail .env*`, blocks `git add *.env`. Exit 1.
3. **Live broker safety** (lines 88-94) — blocks `SAXO_ENV=live` (regex tolerant of whitespace). Exit 2.

Gaps worth knowing:
- Doesn't block the Windows `type` command (PowerShell equivalent of `cat`).
- Doesn't block `git show .env` or `git log -p -- .env` (history leak vectors).
- Doesn't gate cherry-pick/rebase for cross-branch integration safety.

### 6.3 Slash commands (`.claude/commands/`)

Six commands form a Ralph-loop-friendly workflow: `/status`, `/test`, `/lint`, `/format`, `/next-task`, `/review-pr`.

`/review-pr` is the most sophisticated — it diffs against a baseline (staged, branch, or commit), checks against the Forbidden Patterns block in CLAUDE.md, cross-references the glossary, and emits a checklist (Critical / Recommended / Optional) with a one-line verdict.

Missing slash commands worth considering: `/handoff` (the skill exists, no shortcut), `/audit` (run module-map + forbidden-patterns + PR review together), `/risk-check`.

### 6.4 Custom skills

Two project-specific skills under `.claude/skills/`:

- **`audit-module-map`** — verifies `docs/architecture/module-map.md` against actual `engine/src/snapd_invest/` and `cli/src/<project>/` layout, including boundary-discipline grep rules (only `api.py` imports FastAPI, only `persistence.py`/`models.py` import SQLAlchemy, etc.).
- **`check-forbidden-patterns`** — runs the engine and CLI forbidden-pattern catalog over staged diffs, a branch range, or the whole codebase. Outputs Critical / Suspect / Clean sections.

Both are well-written: clear trigger phrases, complete procedures, explicit constraints. Good fit for a Ralph-loop.

### 6.5 `.claude/settings.local.json` — verified safe

The previous reviewer-agent flagged this as **CRITICAL: hardcoded PATs**. Verified false-alarm:

- `.gitignore:102` lists `.claude/settings.local.json` correctly.
- `git ls-files | grep settings.local.json` returns nothing (file is not currently tracked).
- The file was briefly tracked in commit `c7734d9` ("refactor: rename algo_invest -> snapd_invest"), then explicitly untracked in `ddd6b7c` ("chore: untrack .claude/settings.local.json").
- `git log -S"github_pat_"` finds zero historical versions containing PAT strings — i.e. the file never held secrets while tracked.
- The current local-only file does contain ~10 token-shaped strings, but those are personal credentials in a gitignored file. This is the intended usage pattern, not a leak.

What is real and worth a low-priority note: the local file does override the base deny list with broad allow patterns (`Bash(git *)`, `Bash(uv *)`, `Bash(python *)`). If those leak via screenshot or accidental copy, they could enable a malicious actor with shell access to bypass the harness. Tighten to specific commands the user actually uses.

### 6.6 CI workflows (`.github/workflows/`)

Two workflows, both ubuntu-latest:

- **`engine-ci.yml`** — checkout, uv setup with cache, `uv sync --frozen`, ruff check, ruff format --check, mypy src, pytest -v.
- **`cli-ci.yml`** — checkout, setup-dotnet@v4 (10.0.x), restore, `dotnet format --verify-no-changes`, `dotnet build /warnaserror --no-restore`, `dotnet test --no-build`.

Both mirror the Makefile exactly. No drift.

### 6.7 PR template

Sections: Task / Summary / Scope check / Verification / Risk / Notes. Risk block has three trading-specific checkboxes (live trading paths, risk gate logic, new external API calls). Strong template.

Suggested addition: a checkbox "Ran `/review-pr staged` and addressed Critical findings".

### 6.8 Repo-tracked pre-commit hook

`scripts/git-hooks/pre-commit` rejects commits on `main`, `develop`, `release/*`. Installed via `make install-hooks` (sets `core.hooksPath`). Layered with the harness deny list and `.claude/hooks/`. No bypass logging; if `--no-verify` is used the only trail is the absent hook record (acceptable for single-user MVP, would need work for multi-user).

---

## 7. Task queue & Ralph-loop workflow

### 7.1 Inventory

| File | ID | Status | Notes |
|---|---|---|---|
| `_template.md` | — | reference | Standard skeleton |
| `_next.md` | — | active | **Stale — points to superseded T-001 (F-01)** |
| `README.md` | — | active | Lifecycle docs |
| `T-001-saxo-sim-integration.md` | T-001 | superseded | Split into T-001-A (done) + T-001-B (pending) |
| `T-001-B-saxo-trading.md` | T-001-B | pending | The real next task. Spec + plan exist on this branch. |
| `T-002-yfinance-real-data.md` | T-002 | pending | Real market data via yfinance/ccxt |
| `T-003-scheduler-wiring.md` | T-003 | done | 2026-05-14, PR #2 |
| `T-004-e2e-pipeline-test.md` | T-004 | pending | Smoke test against FastAPI |
| `T-005-nswag-contract-client.md` | T-005 | pending | NSwag-generated client |

T-001-A is **not** in `tasks/` — it shipped as spec + plan + PR only. That's the F-04 inconsistency.

### 7.2 Template quality

Sections in `_template.md`: Status / Created / Owner / Blocked by / Context / Acceptance criteria / Files in scope / Out of scope / Verify / Notes.

Missing fields a senior dev would want:

- **Risk / blast radius** — for trading-adjacent work, "touches money?" is a useful pre-flight check.
- **Duration estimate** — README says "≤ 2 hours" as a guideline, but tasks don't carry an estimate field.
- **Depends-on graph** — the `Blocked by` field is freeform text; not machine-parsable.
- **Promotion-gate impact** — does this task move something across paper → SIM → live?

### 7.3 Spec ↔ plan ↔ task ↔ PR linkage

For T-001-A, T-001-B, T-003 the chain is consistent: spec under `docs/specs/`, plan under `docs/plans/`, task under `tasks/` (except T-001-A), PR with task ID in subject or body.

T-001-B's task file (lines 158-159) directly references its spec and plan paths. That's the right pattern — propagate it.

### 7.4 Workflow loop reliability

`/next-task` flow:
1. Read `_next.md` → find next task ID
2. Open task file, verify acceptance criteria
3. Create branch `feature/T-NNN-slug`
4. Implement
5. Run verify commands
6. Commit (conventional format)
7. Push
8. Update task `Status:` and `_next.md`

Failure modes today:
- **Step 1** breaks for an agent because `_next.md` is stale (F-01).
- **Step 8** is purely manual — no automation enforces that `_next.md` is updated when a PR merges. The current stale state is direct evidence the manual step gets skipped.

### 7.5 Promotion gate tracking

ADR-003 says promotion gates are in code. T-001-B spec §4.6 introduces the `PromotionGate` callable (trivial impl for MVP). After T-001-B merges, there's no follow-up task in `tasks/` for "wire eval thresholds into the gate". That's not a problem today, but it's the missing T-006 you'll want to write once eval YAML lands.

### 7.6 Autonomy boundaries

A Ralph-loop agent can:
- pick a task,
- branch,
- code,
- run verify commands,
- commit on a feature branch.

A Ralph-loop agent **must stop** for:
- `git push` (settings.json ask-list)
- PR merge (no auto-merge anywhere)
- Ambiguous acceptance criteria
- Anything requiring Saxo SIM credentials

This matches the user's "ask before mess" posture and is intentional.

---

## 8. Findings by severity

### CRITICAL

(None. The PAT-leak finding flagged during this review was verified false and is folded into F-10 as informational.)

### HIGH

- **F-01** — `tasks/_next.md:6` stale (points at superseded T-001). 5-line fix to point at `T-001-B-saxo-trading.md`. Also update the backlog section.

### MEDIUM

- **F-02** — Two routes (`api.py:640, 760`) execute SQLAlchemy queries directly. Extract `portfolio.get_account_by_id`. Adds symmetry with `get_account_by_name`.
- **F-03** — `RiskConfig.max_daily_loss_pct` is dead config. Implement the check or remove the field. Either is fine, but the current state is misleading.
- **F-04** — T-001-A has no `tasks/` file. Either backfill `tasks/T-001-A-saxo-sim-oauth-and-get-account.md` with `Status: done` for archive symmetry, or amend `tasks/README.md` to acknowledge that spec+plan+PR is a valid alternative path for complex tasks.
- **F-05** — `tasks/T-001-saxo-sim-integration.md` should be moved to `tasks/.archive/` or renamed `T-001-DEPRECATED-*` to prevent accidental pickup.
- **F-06** — `README.md:35` claims `docs/setup.md` is TODO (it isn't); README never links `AGENTS.md`. Two-line fix.
- **F-07** — `docs/architecture/module-map.md` missing `pipeline.py` row. Add one row, owner = `pipeline`, depends on `strategy, execution, agent, recommendation`.

### LOW–MEDIUM

- **F-08** — `--engine-url` doc/impl drift in CLI. Implement or correct the doc.
- **F-09** — Untyped `Dictionary<string, object?>` outcomes in `ApproveCommand` / `RunOnceCommand`. Latent contract bomb. Wait for T-005 (NSwag).

### LOW

- **F-10** — `.claude/settings.local.json` is currently safe (gitignored, never tracked with PAT content). Tighten its `allow` patterns from `Bash(git *)` etc. to specific subcommands at next convenient pass. No urgent action.
- **Glossary gaps** — promotion gate, eval status, watchlist, recommendation state-transition semantics, personality instances missing from `ubiquitous-language.md`.
- **N+1 in `portfolio.build_summary`** — fine at MVP scale.
- **Stale OAuthState rows** — no cleanup job. Add a sweep job alongside the recommendation-expiry job at some point.
- **Hooks coverage** — `pre_tool_bash.py` doesn't block Windows `type .env` or `git show .env`.
- **Missing slash commands** — `/handoff`, `/audit`, `/risk-check` would round out the Ralph-loop set.
- **Docs that future-you will want**: a 10,000-ft `docs/engine-architecture.md` describing the signal→trade flow, a `docs/secrets-handling.md` once tokens are in play, a multi-user-readiness consolidation doc once we cross that bridge.

### INFORMATIONAL

- ADR-006 (OrderResult union) is locked into T-001-B plan Task 1 — must land in the same PR as the implementation.
- "Lessons learned" section in `saxo-openapi-notes.md` is locked into T-001-B plan Task 19.
- Promotion-gate work (eval thresholds) is unscoped beyond MVP; consider writing a placeholder T-006 once T-001-B is merged.

---

## 9. Recommended next steps for the follow-up Claude agent

1. **Fix `tasks/_next.md`** (F-01, F-05). Suggested:
   - Move `tasks/T-001-saxo-sim-integration.md` → `tasks/.archive/T-001-superseded.md` (or rename in-place to `T-001-DEPRECATED-saxo-sim-integration.md`).
   - Update `_next.md:6` to `T-001-B-saxo-trading.md`.
   - Update the backlog section to list T-001-B in slot 1.
   Commit as `docs(tasks): point _next at T-001-B and archive superseded T-001`.

2. **Fix `README.md`** (F-06). Remove `(TODO)` from the setup.md link; add a "Working with this repo: see [`AGENTS.md`](AGENTS.md)" line.

3. **Decide on F-04** (backfill T-001-A task file vs document the alternative path in `tasks/README.md`). Recommend backfilling for symmetry with T-003 and T-001-B.

4. **Pre-T-001-B housekeeping** (before that PR lands):
   - Extract `portfolio.get_account_by_id(session, id)` and migrate `api.py:640, 760` (F-02). The T-001-B work will touch these areas anyway.
   - Add `pipeline.py` row to `docs/architecture/module-map.md` (F-07).
   - Either implement the daily-loss check or remove the `max_daily_loss_pct` field (F-03). If you implement it, the right place is `risk.py:evaluate` consulting `audit_events` for today's realized P&L plus the open-position unrealized — that's significant work; deleting the field is the small-fix path until you're ready to do it properly.

5. **Pick T-001-B and proceed.** The spec (`docs/specs/T-001B-saxo-trading.md`) and plan (`docs/plans/2026-05-16-T-001-B-saxo-trading.md`) are detailed (25 TDD tasks). Use `/next-task` after step 1 is done; otherwise read directly.

6. **After T-001-B merges**, write a placeholder T-006 for promotion-gate eval thresholds so the future work is at least named.

7. **Lower-priority cleanup, when time allows:**
   - Implement or correct `--engine-url` in the CLI (F-08).
   - Tighten `.claude/settings.local.json` allow patterns.
   - Drop the unused `ErrorOr` package or start using it for the Result-type question.
   - Add `/handoff`, `/audit`, `/risk-check` slash commands.
   - Extend `ubiquitous-language.md` with the missing terms.

---

## 10. What the user (Torben) cares about — operating notes

From `~/.claude/CLAUDE.md` and project memory:

- Conversation may be Danish; code/docs in English.
- Senior .NET/DDD architect — talk to him like a peer, push back when justified.
- Boring code over clever code. No DDD ceremony unless a concrete trigger justifies it.
- Open-source LLMs (Ollama) are the default; paid providers opt-in only.
- Multi-user readiness is required from day one in new schemas/auth even though MVP is single-user (per `account_id` scoping).
- He prefers being asked once over fixing a mess afterward.

When you take a step that touches money, broker adapters, or the risk gate, **stop and confirm** even if the task seems to authorize it. Same for anything that modifies `.claude/`, migration history, or `risk.py` thresholds.

---

*End of review.*
