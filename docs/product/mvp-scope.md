# MVP Scope

What's in and what's deliberately out of the snapd-invest MVP.

Update only when scope is intentionally changed. Discuss before adding "just one more thing".

---

## North star

Democratize informed investing by giving non-experts access to multiple agent personalities that propose, explain, and (with explicit approval) execute trades — alongside a deterministic micro-trader.

**This is the long-term vision. The MVP is much narrower.**

---

## In scope for MVP

### User
- **Single user (the author).** No authentication, no authorization, no multi-tenancy.

### Environment
- **Local only.** Runs on the developer's machine. No deployment, no hosting.
- **Paper trading only.** Internal `PaperBroker` simulating fills. No Saxo, no live money.

### Engine
- Market data ingestion from **free sources** (yfinance for stocks/ETFs, ccxt for crypto).
- One deterministic strategy: **SMA Crossover** (the MicroTrader).
- One LLM-powered agent with **one configurable personality**.
- A recommendation queue with explicit approve / reject / modify flow.
- A risk gate enforced on every signal — even human-approved ones.
- An audit log of every signal, gate decision, recommendation lifecycle change, order, and fill.
- Persistence in SQLite.
- Scheduler that runs MicroTrader every minute and the agent every 30 minutes (configurable).

### Client
- A .NET CLI with Spectre.Console output.
- Commands: `status`, `run-once`, `audit`, `recos`, `approve`.

### Quality
- Unit tests with hand-written fakes (no live broker / live LLM in tests).
- One end-to-end test exercising the full pipeline through the API.
- CI runs lint + tests for both stacks on every PR.

---

## Out of scope for MVP

### Trading
- **No live trading.** Anywhere. Even with explicit user approval.
- **No Saxo integration.** Saxo SIM and Saxo live both come later.
- **No real-time market data.** Free EOD and minute-delayed data is enough.
- **No options, futures, FX trading.** Stocks, ETFs, and crypto only.
- **No leverage, no shorting** (paper broker may simulate longs only).

### Strategies and agents
- **No backtesting framework.** Tests cover correctness, not historical performance — that comes when we add the eval suite.
- **No eval YAML promotion gates.** The structure is in mind, not in code yet.
- **No multi-agent orchestration.** Each agent runs independently. No voting, debating, or meta-agent layers.
- **No fine-tuned models.** Off-the-shelf Ollama models only.
- **No agent memory beyond a single run.** Each run starts fresh from current portfolio and data.
- **No grid trading.** Comes after SMA crossover proves the infrastructure works.
- **No classical ML.** Deterministic strategies and LLM agents only.

### User experience
- **No web UI.** CLI only.
- **No mobile.** CLI only.
- **No notifications** (email, push, etc.).
- **No tax reporting.** Audit logs carry enough metadata for later reconstruction, but no K4 / SKAT export yet.
- **No multi-currency portfolio reporting.** Single base currency assumed.

### Operations
- **No cloud deployment.** Local Windows machine only.
- **No observability beyond logs.** No Prometheus, Grafana, distributed tracing.
- **No secret manager.** `.env` files locally; .NET user secrets where appropriate.
- **No backup automation.** SQLite file lives on the developer's machine; manual backup if desired.

---

## Done definition for MVP

The MVP is "done" when, in a single session, the user can:

1. Start the engine and CLI locally.
2. Run `algoinvest status` and see an empty portfolio with cash balance.
3. Run `algoinvest run-once` and see the MicroTrader emit (or correctly skip) a signal based on current market data.
4. Run `algoinvest run-once --agent` and see the agent produce a recommendation.
5. Run `algoinvest recos` and see the pending recommendation with the agent's rationale.
6. Run `algoinvest approve <id>` and see the recommendation execute against the paper broker.
7. Run `algoinvest audit` and trace the entire flow from signal to fill.
8. All of the above with reasonable performance (no command takes > 5s on local data).

When this works end-to-end, MVP is done. Anything else is post-MVP.

---

## Post-MVP roadmap (not commitments — directions)

Rough sequence of what comes after MVP, in expected order:

1. Saxo SIM integration (real Saxo API surface, simulated money).
2. Backtesting framework with historical data.
3. Eval suite with YAML promotion gates.
4. Second and third agent personalities.
5. Grid trading strategy.
6. Saxo live with hard limits (after months of paper + sim evidence).
7. Web UI (Blazor Server).
8. Tax reporting helpers (DK-specific: K4 export, FIFO cost basis).
9. Multi-user architecture review.
10. Cloud deployment (if multi-user is pursued).

Each of these gets its own scope document when it becomes active.
