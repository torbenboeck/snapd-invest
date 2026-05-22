# T-007 — Scheduler SIM-aware (autonomous MicroTrader on Saxo SIM)

**Status:** done
**Created:** 2026-05-21
**Owner:** Claude Code
**Blocked by:** T-001-B (Saxo trading), T-006 (Saxo bar data)

## Context

Today's scheduler resolves every watchlist entry against
`settings.default_account_name` (default `"paper"`) with hardcoded
`instrument_type="stock"` and `currency="USD"`
(`scheduler.py:_resolve_account_and_instrument`). Two consequences:

1. A SIM account never gets a `saxo_uic` populated, so
   `SaxoBroker.place_order` raises `ValueError` on the first signal.
2. FX symbols like `EURDKK@FX` get classified as stock/USD even when
   the configured account is SIM, which trips the Saxo `/ref/v1/instruments`
   lookup later.

This task makes the scheduler SIM-aware:

- Resolve the instrument through `ensure_saxo_instrument` (UIC backfill)
  when the account is `sim`.
- Derive `instrument_type` from the watchlist exchange (`FX` -> `fx`,
  anything else -> `stock`).
- Add a `bar_refresh_tick` job that calls `SaxoBroker.get_charts`
  (delivered by T-006) for SIM accounts and upserts bars into the
  `bars` table, so `SMACrossoverStrategy.load_recent_bars` has
  something to read.

Together with T-006 this is what lets the autonomous MicroTrader
actually emit signals and place SIM orders without manual `place-order`
invocations.

## Acceptance criteria

- [ ] `_resolve_account_and_instrument` branches on
      `account.account_type`:
  - `paper` -> `ensure_instrument(instrument_type=<derived>, currency=account.base_currency)`.
  - `sim` -> `ensure_saxo_instrument(broker, ...)`. Skip with a
    structured warning if `saxo_client_key` / `saxo_account_key`
    aren't backfilled yet.
- [ ] New helper `_instrument_type_for_exchange(exchange)` returns
      `"fx"` for `FX` (case-insensitive) and `"stock"` otherwise. Lives
      in `pipeline.py` next to `parse_watchlist_entry`.
- [ ] `build_default_jobs` registers a 4th job `bar_refresh_tick`:
  - Interval: new setting `bar_refresh_interval_minutes` (default 5).
  - For each watchlist entry: resolve account + instrument exactly
    as `_microtrader_handler` does. If SIM, call `get_charts` for the
    strategy's configured interval (default: `1d`) and upsert via
    `upsert_bars(source="saxo")`.
  - Failures are logged and don't propagate out of the handler.
- [ ] Settings:
  - `bar_refresh_interval_minutes: int = 5` (validated `>= 1`).
  - `bar_refresh_horizon` (`1m` | `5m` | `15m` | `1h` | `1d`,
    default `1d`).
  - `bar_refresh_count: int = 250` (the SMA `long_period` default
    of 200 plus a margin).
- [ ] Unit tests cover the SIM branch resolution, the FX
    `instrument_type` derivation, the bar refresh happy path (mocked
    SaxoBroker), the `saxo_account_key`-missing branch, and the
    "broker raises" failure isolation.

## Files in scope

- `engine/src/snapd_invest/pipeline.py` (add `_instrument_type_for_exchange`)
- `engine/src/snapd_invest/scheduler.py` (resolve + new job)
- `engine/src/snapd_invest/config.py` (new settings)
- `engine/tests/unit/test_pipeline.py` (extend)
- `engine/tests/unit/test_scheduler.py` (extend)
- `start-engine.cmd` (new — Windows entry point, see "Notes")
- `README.md` (one section on the cmd.exe quick-start)

## Out of scope

- yfinance/ccxt providers (still T-002).
- Multi-account scheduler. Today's scheduler still operates against
  the single `settings.default_account_name`; broadening to "all
  accounts" is a separate decision.
- Promotion gate changes — the trivial gate already allows SIM.
- Strategy parameter tuning for FX (FX lot sizes, conviction).

## Verify

```bash
cd engine
uv run ruff check
uv run ruff format --check
uv run mypy src
uv run pytest tests/unit/test_scheduler.py tests/unit/test_pipeline.py -v
```

Manual smoke from a cmd.exe with a configured `.env`:

```cmd
start-engine.cmd
```

Engine should start, the scheduler should announce all four jobs
(`microtrader_tick`, `agent_tick`, `expire_overdue`, `bar_refresh_tick`),
and after one `bar_refresh_tick` interval the `bars` table should
contain rows for the configured watchlist.

## Notes

- The `start-engine.cmd` is the user-requested cmd.exe entry. It
  changes into `engine/`, runs
  `uv run uvicorn snapd_invest.api:app --host 127.0.0.1 --port 8000`,
  and prints a one-line banner with the watchlist + interval the
  engine will operate against. No additional shell magic.
- Default `Horizon` is `1d` because `SMACrossoverConfig.interval`
  defaults to `1d`. Lowering both together (e.g. `1h`) speeds up the
  smoke test against a live SIM but is a deliberate user choice via
  env, not a default.
