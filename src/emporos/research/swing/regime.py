"""The market-trend regime and the rebalance calendar (A1, A2; EM-228, EM-229).

`IndexTrendRegime` is on when the index's close at the decision session is above the simple average
of its last `window` closes (that close included). It reads a reference index (NIFTY 50) that is NOT
an instrument in the dataset: the index has its own series, keyed by date. Until the index has
`window` sessions the regime has no opinion and is OFF (cash), so a run's effective start is the
warm-up's end. Index bars are raw (an index has no splits).

`WeeklyCalendar` and `MonthlyCalendar` rebalance at the close of the first session of each calendar
week or month, the first session in the data included, judged from the dataset's own sessions so a
holiday-shortened week starts on its first open day.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.swing.data import AsOfView
from emporos.research.swing.rules import DecisionContext

__all__ = [
    "IndexSeries", "IndexTrendRegime", "MonthlyCalendar", "RebalanceCalendar", "WeeklyCalendar",
]  # fmt: skip


class IndexSeries:
    """An index's daily closes, oldest first, by session date."""

    def __init__(self, bars: Sequence[Candle]) -> None:
        days = [bar.ts.astimezone(IST).date() for bar in bars]
        if any(later <= earlier for earlier, later in pairwise(days)):
            raise ValueError("an index series is oldest first, one bar per session")
        self._days = days
        self._closes = [bar.close.amount for bar in bars]

    def __len__(self) -> int:
        return len(self._days)

    def closes_up_to(self, day: date, count: int) -> list[Decimal]:
        """The last `count` closes on or before `day` (fewer if the series has fewer)."""
        end = bisect_right(self._days, day)
        return self._closes[max(0, end - count) : end]

    @property
    def first_day(self) -> date | None:
        return self._days[0] if self._days else None


class IndexTrendRegime:
    def __init__(self, index: IndexSeries, window: int = 200) -> None:
        if window < 2:
            raise ValueError("a moving average needs at least two closes")
        self._index = index
        self._window = window

    def is_on(self, context: DecisionContext) -> bool:
        closes = self._index.closes_up_to(context.day, self._window)
        if len(closes) < self._window:
            return False
        return closes[-1] > sum(closes, Decimal(0)) / self._window

    def first_defined_day(self, calendar: Sequence[date]) -> date | None:
        """The first session of `calendar` on which the regime has its full window."""
        for day in calendar:
            if len(self._index.closes_up_to(day, self._window)) >= self._window:
                return day
        return None


class RebalanceCalendar(Protocol):
    def is_rebalance(self, view: AsOfView) -> bool: ...


class _PeriodCalendar(ABC):
    @abstractmethod
    def _period(self, day: date) -> tuple[int, ...]: ...

    def is_rebalance(self, view: AsOfView) -> bool:
        previous = view.previous_session()
        return previous is None or self._period(previous) != self._period(view.day)


class WeeklyCalendar(_PeriodCalendar):
    def _period(self, day: date) -> tuple[int, ...]:
        iso = day.isocalendar()
        return (iso.year, iso.week)


class MonthlyCalendar(_PeriodCalendar):
    def _period(self, day: date) -> tuple[int, ...]:
        return (day.year, day.month)
