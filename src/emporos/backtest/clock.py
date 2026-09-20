"""Simulation time: the clock a backtest runs on (plan.md §10, look-ahead).

`BarClock` stands at the close of the latest bar handed out and can only move FORWARD; a backtest
that tried to rewind it has a bug, so it raises instead of quietly clamping. Strategies never get
the clock itself: they get `view()`, which has `now()` and nothing else, so the code that decides
what "now" is stays out of their reach.
"""

from __future__ import annotations

from datetime import UTC, datetime

from emporos.core.clock import Clock


class TimeTravelError(RuntimeError):
    """The simulation clock was asked to go backwards."""


class ReadOnlyClock:
    """The strategy's window onto simulated time: it can look, never touch."""

    def __init__(self, source: Clock) -> None:
        self._source = source

    def now(self) -> datetime:
        return self._source.now()


class BarClock:
    def __init__(self, start: datetime) -> None:
        self._now = self._utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        """Move to `when`. Standing still is fine (several instruments close together)."""
        when = self._utc(when)
        if when < self._now:
            raise TimeTravelError(
                f"the simulation clock stands at {self._now.isoformat()} and cannot go back to "
                f"{when.isoformat()}"
            )
        self._now = when

    def view(self) -> ReadOnlyClock:
        return ReadOnlyClock(self)

    @staticmethod
    def _utc(when: datetime) -> datetime:
        if when.tzinfo is None or when.utcoffset() != UTC.utcoffset(None):
            raise ValueError("the simulation clock needs timezone-aware UTC times")
        return when
