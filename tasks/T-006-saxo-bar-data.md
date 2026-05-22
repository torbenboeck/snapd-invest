# T-006 — Saxo bar data via /chart/v1/charts

**Status:** done
**Created:** 2026-05-21
**Owner:** Claude Code
**Blocked by:** T-001-B (Saxo trading)

## Context

`SMACrossoverStrategy` and `Agent.run` both read bars via
`load_recent_bars`. Today the `bars` table is empty in a running engine —
there is no scheduled refresh and `YFinanceProvider` (T-002) hasn't shipped
yet. That means the autonomous MicroTrader produces zero signals on a real
engine instance no matter how the watchlist is configured.

For SIM accounts the cleanest source is Saxo's own
`/chart/v1/charts` endpoint: the OAuth client is already authenticated,
the UIC + AssetType are already cached in `Instrument`, and Saxo returns
the same OHLC the trader will execute against. Pulling bars from Saxo for
SIM accounts unblocks T-007 (scheduler SIM-aware) and ultimately
"MicroTrader fully autonomous on Saxo SIM."

`yfinance` for paper accounts is intentionally left to T-002.

## Acceptance criteria

- [ ] `SaxoBroker.get_charts(instrument, interval, count)` calls
      `GET /chart/v1/charts?AssetType=...&Uic=...&Horizon=...&Mode=UpTo&Count=...`
      and returns `list[BarData]`.
- [ ] Supported intervals: `1m`, `5m`, `15m`, `1h` (Horizon=60), `1d`
      (Horizon=1440). Unsupported intervals raise `ValueError`.
- [ ] OHLC is computed as the mid of Bid/Ask for each candle
      (`open = (OpenBid + OpenAsk) / 2`, same for high/low/close).
      Volume is read from `Volume` or defaults to `Decimal(0)` for FX
      (Saxo returns 0 there).
- [ ] Raises `ValueError` if `instrument.saxo_uic` is missing (caller
      must run `ensure_saxo_instrument` first).
- [ ] Goes through `_authed_request` so a 401 triggers token refresh
      like every other Saxo call.
- [ ] Unit tests with `respx` cover: happy path FX (volume=0), happy
      path stock (volume>0), missing UIC, empty `Data`, 401 -> refresh
      retry path.

## Files in scope

- `engine/src/snapd_invest/broker/saxo.py` (add `get_charts`, mapping helper)
- `engine/tests/unit/test_saxo_broker.py` (extend)

## Out of scope

- yfinance / ccxt providers — that's T-002.
- Scheduler bar-refresh job — that's T-007.
- Real-time WebSocket streaming.
- Historical bulk backfill (>1000 bars per call) — `Count` is capped
  at Saxo's per-request limit.

## Verify

```bash
cd engine
uv run ruff check
uv run mypy src
uv run pytest tests/unit/test_saxo_broker.py -k get_charts -v
```
