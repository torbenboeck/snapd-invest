"""Pure indicator functions over price series.

Indicators are not strategies. They are math. Strategies compose indicators
with rules to produce signals.

All indicators accept a sequence of `Decimal` and return a list of `Decimal`
of the same length, with `None`-equivalent leading values represented as
`Decimal("NaN")` (so callers can keep index alignment). Use the helper
`is_nan()` to check.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def is_nan(value: Decimal) -> bool:
    """Whether a Decimal is the NaN sentinel used for "not yet defined"."""
    return value.is_nan()


_NAN = Decimal("NaN")


def sma(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Simple moving average. Output is the same length as `values`.

    Leading `period - 1` elements are NaN (insufficient lookback).
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Decimal] = []
    window: list[Decimal] = []
    running = Decimal("0")
    for v in values:
        window.append(v)
        running += v
        if len(window) > period:
            running -= window.pop(0)
        if len(window) < period:
            out.append(_NAN)
        else:
            out.append((running / Decimal(period)).quantize(Decimal("0.0001")))
    return out


def ema(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Exponential moving average. Standard 2/(period+1) smoothing factor."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    out: list[Decimal] = []
    prev: Decimal | None = None
    for i, v in enumerate(values):
        if i + 1 < period:
            out.append(_NAN)
            continue
        if prev is None:
            # Seed with SMA of the first `period` values
            seed = sum(values[i - period + 1 : i + 1], Decimal("0")) / Decimal(period)
            prev = seed
        else:
            prev = (v * alpha) + (prev * (Decimal(1) - alpha))
        out.append(prev.quantize(Decimal("0.0001")))
    return out


_MIN_CROSSOVER_HISTORY = 2


def crossover(short: Sequence[Decimal], long: Sequence[Decimal]) -> int:
    """Detect a crossover at the *last* index.

    Returns:
        1  if short crossed *above* long between the last two indices (buy signal)
        -1 if short crossed *below* long (sell signal)
        0  otherwise (no crossover, or insufficient data)
    """
    if len(short) != len(long) or len(short) < _MIN_CROSSOVER_HISTORY:
        return 0
    prev_short, prev_long = short[-2], long[-2]
    last_short, last_long = short[-1], long[-1]
    if any(is_nan(v) for v in (prev_short, prev_long, last_short, last_long)):
        return 0
    if prev_short <= prev_long and last_short > last_long:
        return 1
    if prev_short >= prev_long and last_short < last_long:
        return -1
    return 0
