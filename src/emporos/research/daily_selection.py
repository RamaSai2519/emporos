"""Spend the book's capacity on the day's most extreme signals (EM-216, lane L17).

`DailyTopKSelection` takes every scored signal the universe gave (one per instrument per session at
most) and keeps, per session, the `k` with the largest score; ties go to the lower instrument id so
the choice is a function of the signals alone. It chooses from SIGNALS, before fills: a chosen
signal whose order never filled leaves that slot empty, and the next-best signal is not promoted,
because promoting it would use the knowledge that the first did not fill.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from emporos.research.screen_trades import ScreenTrade

__all__ = ["DailyTopKSelection", "ScoredSignal"]


class ScoredSignal(Protocol):
    @property
    def instrument_id(self) -> str: ...

    @property
    def day(self) -> date: ...

    @property
    def score(self) -> Decimal: ...


@dataclass(frozen=True)
class DailyTopKSelection:
    k: int

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError("a selection keeps at least one signal a day")

    def chosen(self, signals: Iterable[ScoredSignal]) -> frozenset[tuple[str, date]]:
        """The (instrument, session) pairs kept."""
        by_day: dict[date, list[ScoredSignal]] = defaultdict(list)
        for signal in signals:
            by_day[signal.day].append(signal)
        kept: set[tuple[str, date]] = set()
        for day, competing in by_day.items():
            ranked = sorted(competing, key=lambda s: (-s.score, s.instrument_id))
            kept.update((s.instrument_id, day) for s in ranked[: self.k])
        return frozenset(kept)

    def keep(
        self, trades: Sequence[ScreenTrade], signals: Iterable[ScoredSignal]
    ) -> list[ScreenTrade]:
        """The trades whose (instrument, session) was chosen, in their original order."""
        chosen = self.chosen(signals)
        return [t for t in trades if (t.instrument_id, t.day) in chosen]
