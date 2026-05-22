# snapd-invest

Hybrid Python + .NET agentic trading platform.

- **Python engine** (`engine/`) — market data, strategies, agents, broker adapters, persistence.
- **.NET CLI** (`cli/`) — user-facing client (Spectre.Console), HTTP client over the engine API.

## Vision

Democratize informed investing by giving non-experts access to multiple agent "personalities" that propose, explain, and (with explicit approval) execute trades on their behalf — alongside a deterministic micro-trader running rule-based intraday strategies.

## Status

Pre-MVP. Single-user, local-only, paper trading. No live trades anywhere.

See:
- [`docs/product/mvp-scope.md`](docs/product/mvp-scope.md) — what's in / out
- [`docs/architecture/decision-log.md`](docs/architecture/decision-log.md) — architectural decisions
- [`docs/ubiquitous-language.md`](docs/ubiquitous-language.md) — canonical terms

## Quick start

```bash
# Python engine
cd engine
uv sync
uv run alembic upgrade head
uv run uvicorn snapd_invest.api:app --reload --port 8000

# .NET CLI (separate terminal)
cd cli
dotnet run --project src/SnapdInvest.Cli -- status
```

### Windows (cmd.exe)

Double-click `start-engine.cmd` or run it from a Command Prompt at the
repo root. It loads `engine\.env` and starts the engine on
`http://127.0.0.1:8000` with the scheduler enabled. To make MicroTrader
run autonomously on Saxo SIM, set in `engine\.env`:

```
SNAPDINVEST_DEFAULT_ACCOUNT_NAME=<your sim account name>
SNAPDINVEST_WATCHLIST=EURDKK@FX
SNAPDINVEST_SAXO_CLIENT_ID=<from Saxo developer portal>
SNAPDINVEST_SAXO_REDIRECT_URI=http://127.0.0.1:8000/v1/oauth/saxo/callback
SNAPDINVEST_ENCRYPTION_KEY=<32-byte Fernet key from `make init-keys`>
```

The scheduler will then refresh Saxo charts into the bar store, run the
SMA crossover strategy on every tick, and place real SIM orders when a
golden / death cross fires — all inside the existing promotion + risk
gates.

Full setup: [`docs/setup.md`](docs/setup.md).

Working with this repo as a Claude Code agent: see [`AGENTS.md`](AGENTS.md).

## Repository layout

```
snapd-invest/
├── engine/                  Python service (FastAPI + APScheduler)
├── cli/                     .NET 10 client (Spectre.Console)
├── docs/                    Architecture, product, language
├── tasks/                   Ralph-loop task queue
├── .claude/                 Claude Code harness (settings, hooks, commands)
├── .github/workflows/       CI for engine and CLI
├── AGENTS.md                How Claude Code works in this repo
├── CLAUDE.md                Root guidance, references submodules
└── README.md                This file
```

## Non-negotiable principles

These are encoded in code, hooks, and CI — not just documentation:

1. **Paper trading by default.** Live trading requires an explicit gate flag plus documented eval evidence.
2. **No autonomous live execution by an LLM agent.** Deterministic strategies may run autonomously within hard limits; LLM agents always require human confirmation before live execution.
3. **Risk gate is always on**, including for human-approved trades.
4. **Every decision is reproducible from audit logs.** No black boxes.
5. **Boring, transparent, testable.** Patterns serve the code, not the other way around.

## License

Private. Not for redistribution.
