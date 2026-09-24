"""A day filter for a scan: trade only on days a regime series calls large (EM-191, EDGE_SEARCH_PLAN
§5 L4).

`TrailingPercentileDays` turns a series of one value per session (the INDIA VIX's 09:15 open) into
the set of days on which that value is at or above a quantile of its OWN previous sessions: today is
never in its own baseline, and with fewer than `minimum` prior sessions no day qualifies, so nothing
looks ahead. `DayGatedRules` wraps any `ScanRules` and drops its entries on days the gate refuses;
the inner rules still see every bar, so their state is exactly what it would be ungated."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from math import ceil
from typing import Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import EntryIntent, ScanIntent, ScanRules

__all__ = ["DayGate", "DayGatedRules", "TrailingPercentileDays", "session_openings"]

_SESSION_OPEN = time(9, 15)


class DayGate(Protocol):
    def allows(self, day: date) -> bool:
        """Whether a scan may enter on this session."""
        ...


def session_openings(bars: Sequence[Candle]) -> dict[date, Decimal]:
    """The open of each session's 09:15 bar. A session without that bar has no opening."""
    out: dict[date, Decimal] = {}
    for bar in bars:
        moment = bar.ts.astimezone(IST)
        if moment.time() == _SESSION_OPEN:
            out[moment.date()] = bar.open.amount
    return out


@dataclass(frozen=True)
class TrailingPercentileDays:
    """The sessions whose value is at or above `percentile` of the previous `lookback` sessions."""

    days: frozenset[date]

    @classmethod
    def from_openings(
        cls,
        openings: Mapping[date, Decimal],
        percentile: Decimal,
        lookback: int = 252,
        minimum: int = 100,
    ) -> TrailingPercentileDays:
        if not Decimal(0) < percentile < Decimal(1):
            raise ValueError("the percentile must be strictly between 0 and 1")
        if not 1 <= minimum <= lookback:
            raise ValueError("need 1 <= minimum <= lookback")
        ordered = sorted(openings.items())
        allowed: set[date] = set()
        for index, (day, value) in enumerate(ordered):
            window = sorted(v for _, v in ordered[max(0, index - lookback) : index])
            if len(window) < minimum:
                continue
            rank = max(ceil(percentile * len(window)) - 1, 0)  # nearest rank
            if value >= window[rank]:
                allowed.add(day)
        return cls(frozenset(allowed))

    def allows(self, day: date) -> bool:
        return day in self.days


class DayGatedRules:
    """`ScanRules` that only enter on the days the gate allows."""

    def __init__(self, inner: ScanRules, gate: DayGate) -> None:
        self._inner = inner
        self._gate = gate

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        intent = self._inner.observe(bar, live=live, held=held, can_afford=can_afford)
        if isinstance(intent, EntryIntent) and not self._gate.allows(bar.ts.astimezone(IST).date()):
            return None
        return intent
