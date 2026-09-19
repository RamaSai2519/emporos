"""Clock abstraction.

Strategies and the execution/risk engines must never call `datetime.now()`
directly — that makes backtests, replay, and deterministic tests impossible.
Everything that needs "now" takes a `Clock` and calls `.now()`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware, UTC."""
        ...


class SystemClock:
    """The real clock. Used in production and paper trading."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that only advances when told to. Used in unit/backtest/replay tests."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = when
