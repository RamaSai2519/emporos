"""The risk-free rate for the IV model: the RBI policy repo rate, dated (EM-244).

`CalendarRepoRate` reads the `rbi_mpc` events of the macro calendar (each carries the repo rate the
MPC decided, in percent). The rate as of a day is the latest decision on or before that day: a
decision is taken before the close, and settle prices are close prices. Days before the first
decision in the calendar (2017-12-06) take that first rate, which had stood since 2017-08-02."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Protocol

from emporos.research.cause_ledger.events import CalendarEvent

__all__ = ["CalendarRepoRate", "RateSource"]


class RateSource(Protocol):
    def rate(self, day: date) -> float:
        """The continuously-compounded-equivalent annual rate as a fraction (0.065 for 6.5%)."""
        ...


class CalendarRepoRate:
    def __init__(self, events: Iterable[CalendarEvent]) -> None:
        self._series = sorted(
            (e.event_date, float(e.value) / 100.0)
            for e in events
            if e.kind == "rbi_mpc" and e.value
        )
        if not self._series:
            raise ValueError("the calendar holds no RBI policy decision with a repo rate")

    def rate(self, day: date) -> float:
        current = self._series[0][1]
        for when, value in self._series:
            if when > day:
                break
            current = value
        return current
