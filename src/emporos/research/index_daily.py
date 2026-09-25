"""Daily closes of an index from its 5-minute bars (EM-225 B-F1, EDGE_SEARCH_PLAN D2).

An index has no daily archive here, only the 5-minute series the D2 fetch cached. A session's close
is the close of its last bar, taken only when that bar is the 15:25 bar (the one that ends the
session at 15:30); a session that stops earlier is not closed and has no daily close, so nothing is
ever priced off a half day."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time
from decimal import Decimal

from emporos.core.clock import IST
from emporos.domain.candles import Candle

__all__ = ["LAST_BAR_START", "daily_closes"]

LAST_BAR_START = time(15, 25)


def daily_closes(bars: Sequence[Candle]) -> dict[date, Decimal]:
    """The close of each session whose last 5-minute bar starts at 15:25, oldest first."""
    closes: dict[date, Decimal] = {}
    for bar in sorted(bars, key=lambda b: b.ts):
        moment = bar.ts.astimezone(IST)
        if moment.time() == LAST_BAR_START and not bar.partial:
            closes[moment.date()] = bar.close.amount
    return closes
