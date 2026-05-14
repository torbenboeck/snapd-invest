"""Tests for `snapd_invest.pipeline`."""

from __future__ import annotations

import pytest

from snapd_invest.pipeline import parse_watchlist_entry


class TestParseWatchlistEntry:
    def test_valid_entry(self) -> None:
        symbol, exchange = parse_watchlist_entry("AAPL@NASDAQ")
        assert symbol == "AAPL"
        assert exchange == "NASDAQ"

    def test_dashes_in_symbol(self) -> None:
        symbol, exchange = parse_watchlist_entry("BTC-USD@BINANCE")
        assert symbol == "BTC-USD"
        assert exchange == "BINANCE"

    def test_strips_whitespace(self) -> None:
        symbol, exchange = parse_watchlist_entry("  AAPL @ NASDAQ  ")
        assert symbol == "AAPL"
        assert exchange == "NASDAQ"

    def test_missing_at_sign(self) -> None:
        with pytest.raises(ValueError, match="SYMBOL@EXCHANGE"):
            parse_watchlist_entry("AAPL")

    def test_empty_symbol(self) -> None:
        with pytest.raises(ValueError, match="empty symbol"):
            parse_watchlist_entry("@NASDAQ")

    def test_empty_exchange(self) -> None:
        with pytest.raises(ValueError, match="empty exchange"):
            parse_watchlist_entry("AAPL@")
