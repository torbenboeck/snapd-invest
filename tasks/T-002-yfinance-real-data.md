# T-002 — Real market data via yfinance

**Status:** pending
**Created:** 2026-05-12
**Owner:** Claude Code
**Blocked by:** —

## Context

The MVP data layer has a `FakeMarketDataProvider` and the `data.py` persistence helpers. We need a real provider hitting `yfinance` (daily + intraday bars for stocks/ETFs) and a `CcxtProvider` for crypto.

Strategies depend on bars being present in the DB. Without a real provider, the MicroTrader has nothing to act on.

## Acceptance criteria

- [ ] `YFinanceProvider` class implementing `IMarketDataProvider`
- [ ] `CcxtProvider` class implementing `IMarketDataProvider`, accepting an exchange name (e.g. "binance")
- [ ] Both providers run inside `asyncio.to_thread()` since the underlying libraries are sync
- [ ] Conversion from provider-specific shapes to `BarData` is well-tested
- [ ] Refresh job registered in `scheduler.py` to refresh bars for a configured watchlist every N minutes
- [ ] Watchlist configurable via env (`SNAPDINVEST_WATCHLIST=AAPL@NASDAQ,BTC-USD@BINANCE,...`)
- [ ] Errors from providers are logged and do not crash the scheduler
- [ ] CLI-visible messages emitted by or about this refresh (errors, status lines, empty-result messages) avoid trader/dev jargon; a non-expert user can understand them. See "## CLI copy guidelines" below.

## Files in scope

- `engine/src/snapd_invest/data.py`
- `engine/src/snapd_invest/scheduler.py`
- `engine/src/snapd_invest/config.py`
- `engine/tests/unit/test_data.py` (extend)
- `cli/src/SnapdInvest.Cli/` (output paths that surface refresh status/errors)

## CLI copy guidelines

Principle: error and status messages should tell the user *what happened in their words* and *what to do next*, not expose internal terminology. The CLI's eventual user is not a trader or developer. Follow the pattern established by `PlaceOrderCommand` (T-001-B) for surfacing engine errors through Spectre.Console.

Use these as a guide, not a closed list — adapt wording (Danish or English) to fit the surrounding copy:

- "OHLC bars" -> "price history"
- "tick size" -> "smallest price step"
- "instrument" -> "stock/symbol you're tracking"
- "rate limited" -> "data provider is asking us to slow down — try again in a minute"
- "yfinance returned no data for SYMBOL@EXCHANGE" -> "Couldn't find any prices for SYMBOL on EXCHANGE. Check the symbol is right, or the market may be closed."
- "watchlist refresh tick" -> "checked for new prices"

## Out of scope

- Real-time WebSocket data
- Paid data providers (Polygon, Tiingo, Finnhub)
- Historical bulk download utility (will arrive with the backtest module)

## Verify

```bash
cd engine
uv run ruff check
uv run mypy src
uv run pytest tests/unit/test_data.py -v
# Ad-hoc manual check (do not commit):
uv run python -c "import asyncio; from snapd_invest.data import YFinanceProvider; print(asyncio.run(YFinanceProvider().fetch_bars(symbol='AAPL', exchange='NASDAQ', interval='1d', limit=5)))"
```
