"""Tests for `snapd_invest.clock`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from snapd_invest.clock import FakeClock, SystemClock


class TestSystemClock:
    def test_now_returns_utc(self) -> None:
        clock = SystemClock()
        now = clock.now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_now_is_close_to_real_time(self) -> None:
        clock = SystemClock()
        now = clock.now()
        real = datetime.now(UTC)
        assert abs((real - now).total_seconds()) < 1.0


class TestFakeClock:
    def test_default_initial_is_2026_01_01_utc(self) -> None:
        clock = FakeClock()
        assert clock.now() == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_initial_must_be_timezone_aware(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            FakeClock(initial=datetime(2026, 1, 1, 12, 0, 0))  # noqa: DTZ001

    def test_set_updates_time(self) -> None:
        clock = FakeClock()
        new_time = datetime(2030, 6, 15, 9, 30, 0, tzinfo=UTC)
        clock.set(new_time)
        assert clock.now() == new_time

    def test_set_requires_timezone_aware(self) -> None:
        clock = FakeClock()
        with pytest.raises(ValueError, match="timezone-aware"):
            clock.set(datetime(2030, 1, 1, 12, 0, 0))  # noqa: DTZ001

    def test_set_normalizes_to_utc(self) -> None:
        clock = FakeClock()
        cet = timezone(timedelta(hours=2))
        clock.set(datetime(2026, 5, 12, 14, 0, 0, tzinfo=cet))
        assert clock.now() == datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)

    def test_advance_by_seconds(self) -> None:
        clock = FakeClock(initial=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        clock.advance(seconds=30)
        assert clock.now() == datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)

    def test_advance_by_minutes_and_hours(self) -> None:
        clock = FakeClock(initial=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        clock.advance(hours=2, minutes=15)
        assert clock.now() == datetime(2026, 1, 1, 2, 15, 0, tzinfo=UTC)

    def test_advance_compounds(self) -> None:
        clock = FakeClock(initial=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        clock.advance(minutes=5)
        clock.advance(minutes=5)
        assert clock.now() == datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)
