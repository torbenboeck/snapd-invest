"""Per-tick orchestration.

This module owns "what happens for one MicroTrader / agent / expire tick".
Both the FastAPI route handlers and the APScheduler-driven jobs delegate
here so there is exactly one code path per concern.

Boundary discipline:
  * No HTTP.
  * No APScheduler.
  * Takes its dependencies as arguments — session, clock, broker, llm,
    risk_config, etc. Does not pull them from app.state or env.
"""

from __future__ import annotations


def parse_watchlist_entry(entry: str) -> tuple[str, str]:
    """Parse one 'SYMBOL@EXCHANGE' string into a (symbol, exchange) tuple.

    Whitespace around the separator and at the ends is stripped. An entry
    is rejected if it has no '@', an empty symbol, or an empty exchange —
    fail-fast at startup is preferable to a silent skip at tick time.
    """
    if "@" not in entry:
        raise ValueError(f"watchlist entry must be in SYMBOL@EXCHANGE format, got {entry!r}")
    symbol, exchange = (part.strip() for part in entry.split("@", maxsplit=1))
    if not symbol:
        raise ValueError(f"watchlist entry has empty symbol: {entry!r}")
    if not exchange:
        raise ValueError(f"watchlist entry has empty exchange: {entry!r}")
    return symbol, exchange
