# Handover — snapd-invest

> Initial scaffold (PR 1-6) produced in a Cowork session on 2026-05-12.
> This document is the entry point for **Claude Code** taking over.

---

## What is this?

A hybrid Python + .NET agentic trading platform. Single-user MVP, paper-trading only.

- **Python engine** (`engine/`) — FastAPI service. Owns trading logic, agents, broker adapters, persistence.
- **.NET CLI** (`cli/`) — Spectre.Console client. UX only. Talks to engine via HTTP.

The product vision, principles, ownership boundaries, and forbidden patterns are described in detail in [`CLAUDE.md`](CLAUDE.md) at the repo root. **Read it first.**

## What's in the scaffold

| PR | Scope | State |
|---|---|---|
| 1 | Repo scaffold + Claude harness (CLAUDE.md hierarchy, `.claude/` settings + hooks + commands, ubiquitous-language, ADRs, MVP scope, CI, Makefile, .editorconfig) | ✅ |
| 2 | Python engine bootstrap (uv, Settings, Clock, persistence, AuditEvent, structlog, FastAPI skeleton, Alembic) | ✅ |
| 3 | Market data, paper broker, portfolio, risk gate (+ Instrument, Bar, Account, Position, Order, Trade models + migration) | ✅ |
| 4 | MicroTrader strategy (indicators, SMACrossoverStrategy, execution pipeline, APScheduler factory) | ✅ |
| 5 | Agentic agent stub (ILlmProvider, OllamaProvider, FakeLlmProvider, Agent with personality, Recommendation lifecycle) | ✅ |
| 6 | .NET CLI (Spectre.Console, Refit client, 7 commands: status, run-once, run-agent, audit, recos, approve, reject) | ✅ |

## What's NOT in the scaffold (deliberately)

- Live Saxo integration
- Real market data fetching (only the persistence + abstraction layer + a `FakeMarketDataProvider` for tests)
- The scheduler actually being started by the FastAPI lifespan
- Backtesting framework
- Eval suite / YAML promotion gates
- Web UI / mobile
- NSwag-generated .NET client (we have a hand-written Refit interface)
- An end-to-end pipeline test (only unit tests so far)
- Real e2e tests

These are tracked as concrete tasks in `tasks/`. See **Next steps** below.

## Repository layout

```
snapd-invest/
├── engine/                    Python service
│   ├── src/snapd_invest/       Flat modules
│   ├── tests/unit/            One test file per module (currently ~80+ tests)
│   ├── alembic/versions/      Migrations 0001-0003
│   └── pyproject.toml         uv config + ruff + mypy + pytest
├── cli/                       .NET 10 client
│   ├── src/SnapdInvest.Cli/    Spectre.Console host + commands
│   ├── src/SnapdInvest.Client/ Refit interface + DTOs
│   ├── tests/                 xUnit + Shouldly + NSubstitute
│   ├── SnapdInvest.sln
│   └── Directory.Packages.props
├── docs/
│   ├── ubiquitous-language.md
│   ├── setup.md
│   ├── architecture/{decision-log,module-map}.md
│   └── product/mvp-scope.md
├── tasks/                     Ralph-loop tasks (T-001 .. T-005 pre-seeded)
├── .claude/                   Harness: settings.json, hooks, slash commands
├── .github/workflows/         engine-ci.yml, cli-ci.yml
├── AGENTS.md                  How Claude Code works in this repo
├── CLAUDE.md                  Root guidance
├── HANDOVER.md                This file
├── Directory.Build.props      .NET solution-wide settings
├── Makefile
└── README.md
```

## Important conventions (encoded in CLAUDE.md hierarchy)

- **Code is English**, conversation may be Danish.
- **Pragmatic, ultra-light architecture.** No DDD ceremony unless a concrete trigger justifies it (see `docs/architecture/decision-log.md` ADR-002).
- **No `datetime.utcnow()` / `DateTime.UtcNow`** in production code — always inject the clock.
- **No live broker / live LLM in tests.** Use `PaperBroker`, `FakeLlmProvider`, `FakeMarketDataProvider`.
- **No business logic in `api.py`** or the .NET CLI. The CLI is presentation only.
- **Branch protection:** `main`/`develop`/`release/*` may not be force-pushed or deleted, server- and locally-enforced.
- **Idempotency:** every Order has a deterministic idempotency key derived from signal content.
- **The risk gate is always on.** Including for human-approved trades.

Read `AGENTS.md` for the operating manual (commit conventions, task workflow, hard rules).

## First-time setup on a fresh machine

```bash
# Prerequisites: Python 3.12+, .NET 10 SDK, uv, git, (optional) Ollama
git clone <repo-url>
cd snapd-invest

# Engine
cd engine
uv sync
uv run alembic upgrade head
uv run pytest                # confirms ~80+ tests pass
cd ..

# CLI
cd cli
dotnet restore
dotnet build /warnaserror
dotnet test                  # confirms tests pass
cd ..

# Verify end-to-end manually
cd engine && uv run uvicorn snapd_invest.api:app --port 8000  # leave running
# (another terminal)
cd cli && dotnet run --project src/SnapdInvest.Cli -- status
```

If anything in the scaffold fails on first run, **fix it before adding features**. The first concrete task is to make `make test` green.

## Next steps (in priority order)

The `tasks/` directory has five concrete tasks pre-seeded. Pick them up via the `/next-task` slash command, or read `tasks/_next.md`:

1. **T-001 — Saxo SIM integration.** Add `SaxoBroker` implementing `IBroker`, OAuth2 against SIM only, env-gated. Single largest unblocker for "real" trading flow.
2. **T-002 — Real market data via yfinance/ccxt.** The pipeline currently has no live bars to chew on.
3. **T-003 — Wire the scheduler into FastAPI lifespan.** Makes the system autonomous (within paper limits).
4. **T-004 — End-to-end pipeline test.** Smoke test against the FastAPI surface using in-memory SQLite and FakeLlmProvider.
5. **T-005 — NSwag-generated .NET client.** Eliminates manual contract drift.

After these five, the natural follow-ups are: a second agent personality, the backtesting framework, the eval YAML promotion gates, then Saxo SIM-to-live promotion. The user's curriculum runs in weekly iterations — typically one task per session.

## Working agreement with the user

The user is an experienced .NET / Azure / DDD architect. Communication style:

- Danish in chat, English in code.
- Pragmatic over academic. Concrete examples over theory.
- Welcomes pushback on assumptions when justified.
- Prefers structured, terse responses with bullets / sections / code blocks.
- "Boring code over clever code" is a stated principle, not a slogan.
- Final decisions are the user's. Claude must ask before making decisions outside the task's stated scope.

The user prefers Claude Code to work in **feature branches** (`feature/T-NNN-*` or `bugfix/T-NNN-*`), one PR per task, with a clean commit history. CI must be green before merge.

## Known follow-ups / open questions

- **PDF storage strategy** is not relevant here (that was for Snapd) — ignore.
- **Saxo OpenAPI onboarding** is pending the user's Saxo dev account approval. Work on T-001 against the documented API surface using mocks until live access is available.
- **News/sentiment data source** for richer agent context is undecided. Free tiers of Marketaux, Finnhub, NewsAPI are candidates. Don't pick one without asking.
- **Local LLM model** is currently set to `llama3.1` (Ollama). Worth experimenting with `qwen2.5`, `mistral-nemo`, `phi3` once we see how reliable JSON output is.

## A note on the harness

`.claude/settings.json` denies destructive git operations (force-push, branch deletion on protected branches, repo deletion, etc.). `.claude/hooks/pre_tool_bash.py` validates context-aware constraints (branch-deletion only for merged `feature/*` and `bugfix/*`; refuses to print `.env`; refuses `SAXO_ENV=live`).

Slash commands live in `.claude/commands/`:

- `/test` — run all tests
- `/lint` — run lint checks (no auto-fix)
- `/format` — auto-format both stacks
- `/status` — repo snapshot
- `/next-task` — pick the next available task and start working on it

The harness is intentionally restrictive — favor asking the user over working around a block.

---

## First-run protocol (do this before anything else)

The Cowork scaffold has not yet been verified on a real machine. Your first job is to bring the entire suite to green and commit the fixes. Steps:

### Python engine

```powershell
cd engine
uv sync                                  # install deps
uv run alembic upgrade head              # apply migrations (creates data/snapd_invest.db)
uv run ruff check                        # lint
uv run ruff format --check               # format check
uv run mypy src                          # type check
uv run pytest -v                         # unit tests
cd ..
```

### .NET CLI

```powershell
cd cli
dotnet restore
dotnet build /warnaserror
dotnet test
dotnet format --verify-no-changes
cd ..
```

### Rules while bringing it green

- **Auto-fix without asking**: ruff format violations, ruff `--fix`-able lint warnings, missing imports, simple type-hint corrections, pyproject/Directory.Packages.props version bumps when a transitive dependency forces a newer version, missing `__init__.py`, missing test fixtures.
- **Fix and note in commit**: small refactors needed to make mypy happy (e.g. adding `# type: ignore[...]` with explanation, narrowing a `Union`), Pydantic V2 API drift (e.g. `model_config` instead of `Config`, `_env_file` keyword changes).
- **Stop and ask** before doing any of: changing public API shapes (FastAPI endpoints, Refit interface, ORM column types), modifying `risk.py` thresholds, modifying anything in `.claude/`, changing the migration history, removing or skipping tests.

### Commit cadence

Use one commit per area of fix with conventional commit messages:

- `chore(engine): fix initial-scaffold lint and type issues from Cowork handover`
- `chore(cli): fix initial-scaffold build issues from Cowork handover`
- `fix(<scope>): <specific issue>` for anything non-trivial

Keep the commit graph linear: a single feature branch is fine for this bootstrap pass, e.g. `chore/handover-greening`. Open a PR to `main` when everything is green.

### When everything is green

Report a summary back to the user: how many tests passed in each stack, what (if anything) had to be fixed, and whether anything was deferred. Then ask whether to proceed with T-001 (Saxo SIM-integration) or something else from the backlog.

If something cannot be resolved without architectural input, stop, write up the question concretely (file, line, why it's blocking), and ask.

---

Good luck.
