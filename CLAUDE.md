# snapd-invest — Root Repository Guidance

Hybrid Python + .NET agentic trading platform. Single-user MVP, paper-trading only.

Personal preferences live in `~/.claude/CLAUDE.md` and apply automatically.

---

## Product Context

snapd-invest gives a single user access to two complementary trading systems:

- **MicroTrader** — deterministic, rule-based strategy engine. Runs autonomously within hard risk limits. Reacts to short-term price movements.
- **Agentic agents** — LLM-powered analysts with distinct "personalities" (e.g. conservative, value-oriented, momentum-driven) and interest areas (cleantech, defence, bonds, etc.). They produce **recommendations** that the user reviews and approves before execution.

Both can target three execution venues, gated by promotion rules: internal paper broker → Saxo SIM → Saxo live. Live is never enabled by default.

Long-term vision: democratize informed investing for non-experts. MVP scope is single-user, local, paper-only — see [`docs/product/mvp-scope.md`](docs/product/mvp-scope.md).

---

## Repository structure

```
engine/    Python 3.12+ service. Owns trading logic, agents, persistence, broker adapters.
cli/       .NET 10 client. UX only. Talks to engine via HTTP.
docs/      Architecture decisions, product scope, glossary.
tasks/     Ralph-loop task queue (markdown files Claude can pick up autonomously).
.claude/   Claude Code harness: settings, hooks, slash commands, sub-agents.
```

Module-specific guidance:
- Python engine: [`engine/CLAUDE.md`](engine/CLAUDE.md)
- .NET CLI: [`cli/CLAUDE.md`](cli/CLAUDE.md)

When working in a subdirectory, Claude Code loads the nearest `CLAUDE.md` automatically.

---

## Ownership boundaries

| Concern | Owner |
|---|---|
| Saxo API calls | **Python** (`engine/`). .NET never talks to Saxo. |
| LLM agent execution | **Python** (`agent.py`) |
| Strategy execution (deterministic) | **Python** (`strategy.py`) |
| Trading data persistence | **Python** (SQLite via SQLAlchemy) |
| User-facing UX | **.NET** (`cli/`) |
| Recommendation approval flow | **Python** owns state; .NET drives via HTTP |

Rule of thumb: if it touches money, it's Python. If it's about what the user sees, it's .NET.

---

## Core terms

The full glossary lives in [`docs/ubiquitous-language.md`](docs/ubiquitous-language.md). Five terms used everywhere:

- **Signal** — a proposed action emitted by a strategy or agent. Has direction, instrument, conviction, rationale. Not yet vetted.
- **Recommendation** — a Signal (or set of signals) packaged for human review with explicit accept/reject/modify lifecycle.
- **Order** — a request to a broker to execute a trade. Has idempotency key and lifecycle.
- **Trade** — a completed fill from the broker.
- **Position** — current holding of an instrument in an account.

---

## Non-negotiable principles

If a task seems to require breaking one of these, **stop and ask**.

1. **Paper trading is the default.** Live trading requires an explicit gate flag plus eval evidence.
2. **No LLM agent executes a live trade autonomously.** Ever. Deterministic strategies (MicroTrader) may run autonomously within hard limits.
3. **Backtesting before paper-with-stakes; paper-with-stakes before live.** Each strategy carries a promotion gate in code.
4. **Risk gate is always on.** Including for human-approved trades — fingerfejl is a real risk.
5. **Every decision is reproducible from audit logs.** Signals, gate decisions, recommendations, approvals, orders, fills — all logged immutably.
6. **Boring code over clever code.** Patterns serve the code. No DDD ceremony, no over-abstraction.
7. **Separation of concerns:** Market data → signal → risk gate → order management → execution. Each step isolated and testable.

---

## Forbidden patterns

- **No live broker calls from tests.** Tests use `PaperBroker` or `FakeBroker`.
- **No live LLM calls in CI.** Tests use `FakeLlmProvider` or recorded responses.
- **No real `datetime.utcnow()` / `DateTime.UtcNow` in domain code.** Use injected clock (Python: `Clock` protocol; .NET: `TimeProvider`).
- **No secrets in code or git.** Use environment variables locally; user secrets for .NET; a `.env` file (gitignored) for Python.
- **No direct DB access from FastAPI route handlers.** Route → service function → persistence.
- **No `print()` in Python production code.** Use `structlog`.
- **Never delete or force-push to `main`, `develop`, or `release/*`.** Server-enforced via branch protection; locally enforced via `.claude/settings.json`.
- **No raw `Guid.NewGuid()` / `uuid.uuid4()` inside business logic.** Inject an ID generator (testability).

---

## Workflow — Ralph-loop friendly

Work items live as markdown files in `tasks/`. Each task has:
- A clear acceptance criterion ("how do we know it's done")
- A short list of files in scope
- Test commands to verify

Claude Code can pick the next task autonomously by:
1. Reading `tasks/_next.md` (or `TaskList` if Cowork) for the next unblocked item
2. Implementing
3. Running `make test` (or the task's own test command)
4. Committing on a feature branch
5. Marking the task done

See [`AGENTS.md`](AGENTS.md) for the operating manual.

---

## Commands

| Action | Command |
|---|---|
| Run engine tests | `cd engine && uv run pytest` |
| Run engine lint | `cd engine && uv run ruff check && uv run ruff format --check` |
| Run CLI tests | `cd cli && dotnet test` |
| Run CLI format check | `cd cli && dotnet format --verify-no-changes` |
| Run all tests (both stacks) | `make test` (when available) |
| Start engine locally | `cd engine && uv run uvicorn snapd_invest.api:app --reload --port 8000` |
| Run CLI command | `cd cli && dotnet run --project src/SnapdInvest.Cli -- <command>` |

---

## Open questions

Flag any open question outstanding for more than 30 days.

- **Saxo OpenAPI onboarding** — pending Saxo dev account approval.
- **News/sentiment data source for agents** — free tier of which provider? (NewsAPI, Marketaux, Finnhub all have free tiers with limits.)
- **Local LLM choice** — Ollama with which model? (llama3.1, mistral, qwen?)
- **Tax reporting (DK)** — out of MVP scope, but tracked for later. Each fill must carry enough metadata to reconstruct cost basis in FIFO.

---

## Documentation maintenance

Update when meaningful changes happen:

- `docs/ubiquitous-language.md` — new canonical terms
- `docs/architecture/decision-log.md` — real architectural decisions (ADR-lite, append-only)
- `docs/architecture/module-map.md` — module responsibilities change materially
- `docs/product/mvp-scope.md` — MVP scope intentionally changed

Do not make cosmetic doc edits without clear value.
