# Ubiquitous Language

Canonical glossary for snapd-invest. Code, API contracts, database tables, log messages, and documentation must use these terms consistently.

When a term is renamed or refined, **update this file first**, then refactor the code.

When a new concept is introduced, add it here at the same time you add the code.

---

## Trading primitives

### Instrument
A tradable financial asset. Identified by `symbol` plus `exchange`. Examples: `AAPL@NASDAQ`, `NOVO-B@CSE`, `BTC-USD@BINANCE`.

Fields: `symbol`, `exchange`, `instrument_type` (`stock` | `etf` | `bond` | `crypto` | `fx` | `future`), `currency`, `tick_size`.

### Bar
One time-bucketed OHLCV record for an instrument. Has `interval` (`1m` | `5m` | `1h` | `1d` etc.).

Fields: `instrument`, `interval`, `timestamp`, `open`, `high`, `low`, `close`, `volume`.

### Tick
A real-time price update. Not stored long-term — only buffered in memory.

### Indicator
A pure mathematical function over bars. Examples: SMA, EMA, RSI, ATR, MACD.

Lives in `engine/src/snapd_invest/indicators.py`. **Indicators are not strategies.**

---

## Decision pipeline

### Signal
A proposed action emitted by a **strategy** or **agent**. Carries:

- `source` — which strategy/agent emitted it
- `instrument`
- `action` — `buy` | `sell` | `hold` | `close`
- `quantity` (or `quantity_pct` of account)
- `conviction` — 0.0–1.0
- `rationale` — text, machine- or LLM-generated
- `timestamp`
- `correlation_id`

A signal has **not been vetted** by the risk gate. It is a proposal.

### Strategy
A deterministic rule-based signal generator. Examples: `SMACrossoverStrategy`, `GridStrategy`, `RSIRevertStrategy`.

Strategies are **pure functions** over market data + portfolio state → signals. No I/O inside the strategy itself.

Lives in `engine/src/snapd_invest/strategy.py`.

### Agent
An LLM-powered signal generator with a configured **personality** and **interests**. Produces signals by reasoning over context (portfolio, market state, news).

Agent output is wrapped in a **recommendation** before reaching the user.

Lives in `engine/src/snapd_invest/agent.py`.

### Personality
A bundle of dispositions applied to an agent: risk tolerance, time horizon, conviction threshold, preferred instruments. Encoded as a prompt template + a typed config object (e.g. `Personality(name="conservative_value", risk=0.3, horizon_days=90, ...)`).

### Interest
A topic or sector an agent focuses on. Examples: `cleantech`, `defence`, `bonds`, `dk_smallcap`. Filters the instrument universe the agent considers.

### Risk Gate
The single point through which **all** signals pass before becoming orders. Validates: position sizing, max daily loss, instrument allowlist, environment consistency (paper vs sim vs live), and the kill switch state.

Lives in `engine/src/snapd_invest/risk.py`. **Always on.** Including for human-approved trades.

### Recommendation
A signal (or set of signals) that requires human approval before execution. Has lifecycle:

`pending` → `approved` | `modified` | `rejected` | `expired`

Modifications: the user may change quantity, price, or instrument before approval. The modified version is what executes.

Lives in `engine/src/snapd_invest/recommendation.py`.

### Order
A request to a broker to execute a trade. Carries `idempotency_key`. Lifecycle:

`draft` → `submitted` → `partially_filled` | `filled` | `rejected` | `cancelled`

### Trade
A completed (fully or partially) execution of an order. Immutable once recorded.

Fields: `order_id`, `fill_price`, `fill_quantity`, `timestamp`, `fees`, `venue`.

### Position
Current holding of an instrument in an account. Computed from trades.

Has a **tag**:
- `managed` — agent may rebalance freely (within risk gate)
- `view_only` — agent may report on but not propose changes
- `untouchable` — agent must ignore entirely

Default for positions that existed before the user opted in: `view_only`.

---

## Execution venues

### Account
A logical container for cash and positions. Has `type`:

- `paper` — internal simulation, no external system involved
- `sim` — Saxo SIM sandbox (real Saxo API, simulated money)
- `live` — Saxo live (real money)

A user can have multiple accounts. Agents and strategies are bound to specific account(s).

### Broker
Abstraction over an execution venue. Implementations: `PaperBroker`, `SaxoBroker`. All implement the `IBroker` protocol.

### Environment
The combination of broker + account type an agent or strategy operates against. Cannot mix in a single run — an agent targets `paper`, `sim`, or `live` deterministically.

---

## Quality gates

### Backtest
A run of a strategy over historical bars, producing performance metrics (Sharpe, max drawdown, win rate, total return). No live or paper effects.

### Paper run
A strategy running live against the internal `paper` broker. Real-time data, simulated execution. Tracked over time and evaluated periodically.

### Eval
A named check with concrete thresholds (e.g. `min_sharpe`, `max_drawdown`). A strategy passes or fails each eval.

### Promotion
The act of moving a strategy from one environment to a more privileged one (paper → sim → live). Requires the eval gates configured for the target environment to be green.

### Kill switch
A global flag that, when set, prevents any new orders from being submitted to any broker. Existing orders are not cancelled automatically — that's a manual step. Can be triggered manually or by an automated condition (e.g. daily loss exceeds threshold).

---

## Audit

### AuditEvent
An immutable log entry for any decision or action in the system. Every signal emission, gate decision, recommendation lifecycle change, order submission, and fill produces an audit event.

Fields: `id`, `type`, `payload` (JSON), `correlation_id`, `timestamp`.

Audit events are **append-only**. They are never updated or deleted.

---

## Conventions

- All timestamps are **UTC**, stored as ISO-8601 in JSON and as `DateTime` (UTC) in databases.
- All monetary values use `Decimal` (Python) / `decimal` (.NET) — never `float`/`double`.
- All quantities use `Decimal` (Python) / `decimal` (.NET).
- Currency codes are **ISO 4217** (`USD`, `DKK`, `EUR`).
- Instrument identifiers follow `SYMBOL@EXCHANGE` format in user-facing strings; internal models use separate fields.
- All IDs are generated at the boundary (API layer or factory), not inside business logic, for testability.
