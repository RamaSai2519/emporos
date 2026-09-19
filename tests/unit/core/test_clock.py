from datetime import UTC, datetime, timedelta

import pytest

from emporos.core.clock import FixedClock, SystemClock


def test_system_clock_returns_timezone_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_fixed_clock_does_not_advance_on_its_own() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FixedClock(start)
    assert clock.now() == start
    assert clock.now() == start


def test_fixed_clock_advance() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FixedClock(start)
    clock.advance(timedelta(minutes=5))
    assert clock.now() == start + timedelta(minutes=5)


def test_fixed_clock_set() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(start)
    clock.set(later)
    assert clock.now() == later


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 1))
