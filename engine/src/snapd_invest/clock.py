"""Injectable clock abstraction.

Production code never calls `datetime.utcnow()` or `datetime.now()` directly.
It depends on the `Clock` protocol so tests can substitute a deterministic
`FakeClock`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of current UTC time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC `datetime`."""
        ...


class SystemClock:
    """Real system clock. Use in production."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Deterministic clock for tests.

    Time advances only when the test explicitly calls `advance(...)` or
    overrides via `set(...)`. By default, returns the time it was constructed
    with.
    """

    def __init__(self, *, initial: datetime | None = None) -> None:
        if initial is None:
            initial = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        if initial.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        self._now = initial.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("set() requires a timezone-aware datetime")
        self._now = when.astimezone(UTC)

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0) -> None:
        delta = timedelta(seconds=seconds, minutes=minutes, hours=hours)
        self._now = self._now + delta
