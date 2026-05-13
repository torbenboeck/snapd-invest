"""Tests for `snapd_invest.indicators`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from snapd_invest.indicators import crossover, ema, is_nan, sma


def D(s: str) -> Decimal:  # noqa: N802 - shorthand
    return Decimal(s)


class TestSMA:
    def test_rejects_non_positive_period(self) -> None:
        with pytest.raises(ValueError, match="period"):
            sma([D("1")], 0)

    def test_short_input(self) -> None:
        out = sma([D("1"), D("2")], 3)
        assert all(is_nan(v) for v in out)

    def test_basic(self) -> None:
        values = [D("1"), D("2"), D("3"), D("4"), D("5")]
        out = sma(values, 3)
        assert is_nan(out[0])
        assert is_nan(out[1])
        assert out[2] == D("2.0000")
        assert out[3] == D("3.0000")
        assert out[4] == D("4.0000")

    def test_keeps_length(self) -> None:
        out = sma([D("1")] * 10, 5)
        assert len(out) == 10


class TestEMA:
    def test_rejects_non_positive_period(self) -> None:
        with pytest.raises(ValueError, match="period"):
            ema([D("1")], 0)

    def test_empty_returns_empty(self) -> None:
        assert ema([], 5) == []

    def test_keeps_length(self) -> None:
        out = ema([D(str(i)) for i in range(20)], 5)
        assert len(out) == 20

    def test_first_period_minus_one_are_nan(self) -> None:
        out = ema([D(str(i)) for i in range(10)], 5)
        for v in out[:4]:
            assert is_nan(v)
        for v in out[4:]:
            assert not is_nan(v)


class TestCrossover:
    def test_no_signal_when_too_short(self) -> None:
        assert crossover([D("1")], [D("1")]) == 0

    def test_bullish_cross(self) -> None:
        short = [D("1"), D("3")]
        long = [D("2"), D("2")]
        assert crossover(short, long) == 1

    def test_bearish_cross(self) -> None:
        short = [D("3"), D("1")]
        long = [D("2"), D("2")]
        assert crossover(short, long) == -1

    def test_no_cross_when_no_change_in_relation(self) -> None:
        assert crossover([D("3"), D("4")], [D("2"), D("2")]) == 0

    def test_nan_returns_zero(self) -> None:
        assert crossover([Decimal("NaN"), D("3")], [D("2"), D("2")]) == 0
