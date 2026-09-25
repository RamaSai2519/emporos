"""Regime rules for opening a spread, from daily index series (EM-226, PROFIT_PLAN.md §5 B1/B2).

Every condition looks only at closes up to and including the decision day (a trend), or only at the
days BEFORE it (a percentile band: today is never in its own baseline), and stays false until it has
the history it needs, so nothing looks ahead and nothing trades on a half-formed average.

`DayCondition`s answer one question about a day. Adapters turn them into the `EntryFilter` and
`DepthPolicy` the backtester composes: a filter refuses a day, a depth policy opens an extra slot
only when a conviction condition holds."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import ceil
from typing import Protocol

from emporos.options.chain import ChainSnapshot

__all__ = [
    "AllOf",
    "ConditionFilter",
    "ConvictionDepth",
    "DayCondition",
    "FirstSessionOfWeek",
    "NoEventThroughExpiry",
    "SmaStack",
    "TrailingPercentileBand",
    "TrendAboveSma",
    "VixVolatility",
]


class DayCondition(Protocol):
    def holds(self, day: date) -> bool: ...


class _Series:
    """A close per session, with the position of each session in the run of sessions."""

    def __init__(self, closes: Mapping[date, Decimal]) -> None:
        self._days = sorted(closes)
        self._position = {d: i for i, d in enumerate(self._days)}
        self._values = [closes[d] for d in self._days]

    def position(self, day: date) -> int | None:
        return self._position.get(day)

    def window(self, end: int, length: int) -> list[Decimal] | None:
        """The `length` values ending at position `end` inclusive, or none if too few exist."""
        if end + 1 < length:
            return None
        return self._values[end + 1 - length : end + 1]

    def value(self, position: int) -> Decimal:
        return self._values[position]


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


class TrendAboveSma:
    """The close is above its simple average of the last `window` sessions, today included."""

    def __init__(self, closes: Mapping[date, Decimal], window: int = 200) -> None:
        if window < 2:
            raise ValueError("an average needs at least two sessions")
        self._series = _Series(closes)
        self._window = window

    def holds(self, day: date) -> bool:
        at = self._series.position(day)
        if at is None:
            return False
        values = self._series.window(at, self._window)
        return values is not None and self._series.value(at) > _mean(values)


class SmaStack:
    """The close is above its `fast` average, which is above its `slow` average."""

    def __init__(self, closes: Mapping[date, Decimal], fast: int = 50, slow: int = 200) -> None:
        if not 2 <= fast < slow:
            raise ValueError("need 2 <= fast < slow")
        self._series = _Series(closes)
        self._fast, self._slow = fast, slow

    def holds(self, day: date) -> bool:
        at = self._series.position(day)
        if at is None:
            return False
        fast = self._series.window(at, self._fast)
        slow = self._series.window(at, self._slow)
        if fast is None or slow is None:
            return False
        return self._series.value(at) > _mean(fast) > _mean(slow)


class TrailingPercentileBand:
    """The day's value lies between two quantiles of the previous `lookback` sessions' values
    (nearest rank, inclusive). Needs a full lookback of earlier sessions; today is never in it."""

    def __init__(
        self,
        values: Mapping[date, Decimal],
        low: Decimal,
        high: Decimal,
        lookback: int = 252,
    ) -> None:
        if not Decimal(0) <= low < high <= Decimal(1):
            raise ValueError("need 0 <= low < high <= 1")
        if lookback < 2:
            raise ValueError("a percentile needs at least two sessions")
        self._series = _Series(values)
        self._low, self._high, self._lookback = low, high, lookback

    def holds(self, day: date) -> bool:
        at = self._series.position(day)
        if at is None or at < self._lookback:
            return False
        earlier = sorted(self._series.window(at - 1, self._lookback) or [])
        if not earlier:
            return False
        value = self._series.value(at)
        return self._quantile(earlier, self._low) <= value <= self._quantile(earlier, self._high)

    @staticmethod
    def _quantile(sorted_values: Sequence[Decimal], q: Decimal) -> Decimal:
        rank = max(ceil(q * len(sorted_values)) - 1, 0)
        return sorted_values[rank]


@dataclass(frozen=True)
class AllOf:
    conditions: tuple[DayCondition, ...]

    def holds(self, day: date) -> bool:
        return all(c.holds(day) for c in self.conditions)


@dataclass(frozen=True)
class ConditionFilter:
    """An `EntryFilter` that opens only on days a condition holds."""

    condition: DayCondition

    def allows(self, snapshot: ChainSnapshot, expiry: date) -> bool:
        return self.condition.holds(snapshot.day)


class FirstSessionOfWeek:
    """Opens only on the first trading day of each ISO calendar week, from the sessions given."""

    def __init__(self, sessions: Iterable[date]) -> None:
        firsts: dict[tuple[int, int], date] = {}
        for day in sorted(sessions):
            iso = day.isocalendar()
            firsts.setdefault((iso.year, iso.week), day)
        self._firsts = frozenset(firsts.values())

    def allows(self, snapshot: ChainSnapshot, expiry: date) -> bool:
        return snapshot.day in self._firsts


class NoEventThroughExpiry:
    """Refuses a spread if any listed event day falls from today through its expiry, inclusive."""

    def __init__(self, events: Iterable[date]) -> None:
        self._events = sorted(set(events))

    def allows(self, snapshot: ChainSnapshot, expiry: date) -> bool:
        return not any(snapshot.day <= e <= expiry for e in self._events)


@dataclass(frozen=True)
class ConvictionDepth:
    """`base` spreads at once, `base + extra` on days the conviction condition holds."""

    base: int
    extra: int
    conviction: DayCondition

    def __post_init__(self) -> None:
        if self.base < 1 or self.extra < 0:
            raise ValueError("need base >= 1 and extra >= 0")

    def limit(self, snapshot: ChainSnapshot) -> int:
        return self.base + (self.extra if self.conviction.holds(snapshot.day) else 0)


class VixVolatility:
    """The India VIX close as the annualised volatility (a close of 15 is 0.15)."""

    def __init__(self, closes: Mapping[date, Decimal]) -> None:
        self._closes = closes

    def annual_volatility(self, day: date) -> Decimal | None:
        close = self._closes.get(day)
        return None if close is None or close <= 0 else close / Decimal(100)
