# Setup

How to get snapd-invest running on a fresh machine.

## Prerequisites

- **Python 3.12+** — the engine
- **.NET 10 SDK** — the CLI
- **uv** — Python package manager. Install: `winget install astral-sh.uv` (Windows) or `curl -LsSf https://astral.sh/uv/install.sh | sh` (Unix)
- **Git** — version control
- **Ollama** (optional, for agent runs) — install from [ollama.com](https://ollama.com). Pull a model: `ollama pull llama3.1`

## One-time setup

```bash
# Clone
git clone <repo-url>
cd snapd-invest

# Engine
cd engine
uv sync
uv run alembic upgrade head
cd ..

# CLI
cd cli
dotnet restore
cd ..
```

## Daily run

Open two terminals:

**Terminal 1 — engine:**
```bash
cd engine
uv run uvicorn snapd_invest.api:app --reload --port 8000
```

**Terminal 2 — CLI:**
```bash
cd cli
dotnet run --project src/SnapdInvest.Cli -- status
```

## Configuration

The engine reads configuration from `engine/.env` (not committed). Copy `engine/.env.example` if it exists, or create:

```bash
# engine/.env
SNAPDINVEST_DB_PATH=./data/snapd_invest.db
SNAPDINVEST_LOG_LEVEL=INFO
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1

# Saxo (later, not required for MVP)
# SAXO_ENV=sim
# SAXO_TOKEN=...
```

The CLI reads `cli/src/SnapdInvest.Cli/appsettings.json` plus `appsettings.Development.json` (not committed). Or use `--engine-url` to override.

## Verifying

```bash
# Engine is up
curl http://localhost:8000/v1/health
# {"status":"ok","version":"0.1.0"}

# Engine responds to CLI
cd cli && dotnet run --project src/SnapdInvest.Cli -- status
```

## Troubleshooting

- **`uv: command not found`** — install uv (see Prerequisites).
- **`Failed to apply migration`** — delete `engine/data/snapd_invest.db` and retry; we're pre-MVP, no production data to protect.
- **`Connection refused` from CLI** — the engine isn't running, or it's on a different port. Check `http://localhost:8000/v1/health`.
- **Ollama errors when running agents** — `ollama serve` must be running, and the configured model must be pulled.
