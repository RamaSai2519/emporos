"""The entry fill, on the program's own intraday rule, in floats (EM-219 S1).

Same rule as `eventtrader.replay.fills.EntryFiller`: a marketable limit `buffer_bps` through the
crossing bar's close (rounded to the tick so it stays marketable), tried on the NEXT 5-minute bar
only; the bar must trade STRICTLY through the limit and have room for the order in `participation`
of its volume; otherwise no trade and no chasing. A test pins it against the Decimal
implementation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from emporos.research.s1.bars import DayBars

__all__ = ["Fill", "Miss", "marketable_limit", "try_entry"]

TICK = 0.05
BUFFER_BPS = 5.0
PARTICIPATION = 0.10
NO_ENTRY_AFTER = 14 * 60 + 45  # the decision minute of the last permitted entry


class Miss(StrEnum):
    NO_BARS = "no_bars"
    LATE = "late"  # after 14:45
    NOT_THROUGH = "not_through"
    NO_VOLUME = "no_volume"


@dataclass(frozen=True)
class Fill:
    index: int  # the filled bar's position in the session
    reference: float  # the crossing bar's close: the entry price before slippage


def marketable_limit(reference: float, buying: bool) -> float:
    moved = reference * (1 + BUFFER_BPS / 10_000 if buying else 1 - BUFFER_BPS / 10_000)
    ticks = moved / TICK
    return (math.ceil(ticks - 1e-9) if buying else math.floor(ticks + 1e-9)) * TICK


def try_entry(day: DayBars, decision_minute: int, buying: bool, quantity: int) -> Fill | Miss:
    """Try to enter on the bar after the one that ended at `decision_minute`."""
    if decision_minute > NO_ENTRY_AFTER:
        return Miss.LATE
    anchor = day.index_ending_at(decision_minute)
    if anchor is None or anchor + 1 >= len(day):
        return Miss.NO_BARS
    index = anchor + 1
    limit = marketable_limit(float(day.close[anchor]), buying)
    through = day.low[index] < limit if buying else day.high[index] > limit
    if not through:
        return Miss.NOT_THROUGH
    if quantity > int(day.volume[index] * PARTICIPATION):
        return Miss.NO_VOLUME
    return Fill(index, float(day.close[anchor]))
