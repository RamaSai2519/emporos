"""Clock abstraction.

Strategies and the execution/risk engines must never call `datetime.now()`
directly — that makes backtests, replay, and deterministic tests impossible.
Everything that needs "now" takes a `Clock` and calls `.now()`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol

# India has no daylight saving, so a fixed offset is exact and needs no tz database.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


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


class Sleeper(Protocol):
    """Pauses the current task. Injected so throttling and backoff run in virtual time in tests."""

    async def sleep(self, seconds: float) -> None: ...


class AsyncioSleeper:
    """The real sleeper."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
