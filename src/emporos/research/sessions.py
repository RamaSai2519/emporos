"""Session-scoped return extraction for intraday lead-lag research (EM-180).

Every function here works with a within-day-ONLY return series: index 0 is the first bar's own
open-to-close move, index i>=1 is that bar's close-to-close move from the bar before it — never
the overnight gap from the previous session's close. That avoids contaminating a "first 15
minutes of today" reading with yesterday's close-to-open jump, and keeps `early_return`/
`subsequent_return`/`late_session_return` exact `Decimal` arithmetic via compounding, the same
technique `emporos.research.factors.compounded_return` uses for the cross-sectional study.

`early_return` and `subsequent_return` never share a bar: `subsequent_return`'s window begins
exactly where `early_return`'s ends (`start.bars`), so a day contributes at most one predictor
reading and one target reading per (early horizon, target horizon) pair — the structural answer
to "prevent overlapping-window leakage" (no sliding per-bar sampling that would let adjacent,
highly-correlated windows count as independent observations).

These functions take a plain return series, not `Candle`s, on purpose: the SAME functions serve a
single stock's own within-day returns and an equal-weighted sector/market aggregate's — whichever
`session_local_returns` (or an average of several) is handed in. That is what lets the lead-lag
engine evaluate stock-level and sector-level expressions without two parallel code paths.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.horizons import Horizon

_ZERO = Decimal(0)
_ONE = Decimal(1)


def split_sessions(bars: Sequence[Candle]) -> list[tuple[date, list[Candle]]]:
    """Groups a sorted bar sequence into one list per IST trading day, in order. Consecutive bars
    on the same IST calendar date are one session; nothing here needs a `TradingCalendar` since it
    only groups bars that already exist, not decide which days should have had one."""
    sessions: list[tuple[date, list[Candle]]] = []
    current_day: date | None = None
    current: list[Candle] = []
    for candle in bars:
        day = candle.ts.astimezone(IST).date()
        if day != current_day:
            if current_day is not None:
                sessions.append((current_day, current))
            current_day, current = day, []
        current.append(candle)
    if current_day is not None:
        sessions.append((current_day, current))
    return sessions


def session_local_returns(day_bars: Sequence[Candle]) -> list[Decimal]:
    """Within-day returns: `[0]` is the first bar's own open-to-close move, `[i]` for `i >= 1` is
    that bar's close-to-close move from the bar before it."""
    first_open = day_bars[0].open.amount
    first = (
        _ZERO
        if first_open == _ZERO
        else DecimalMath.divide(day_bars[0].close.amount - first_open, first_open)
    )
    returns = [first]
    for i in range(1, len(day_bars)):
        previous = day_bars[i - 1].close.amount
        returns.append(
            _ZERO
            if previous == _ZERO
            else DecimalMath.divide(day_bars[i].close.amount - previous, previous)
        )
    return returns


def _compound(returns: Sequence[Decimal]) -> Decimal:
    product = _ONE
    for value in returns:
        product *= _ONE + value
    return product - _ONE


def early_return(returns: Sequence[Decimal], horizon: Horizon) -> Decimal | None:
    """The return from the session's own open to the close of the bar `horizon.bars` into it —
    the "first N minutes" predictor. `None` if the session has fewer bars than that."""
    if horizon.bars < 1 or horizon.bars > len(returns):
        return None
    return _compound(returns[: horizon.bars])


def subsequent_return(
    returns: Sequence[Decimal], start: Horizon, span: Horizon
) -> Decimal | None:
    """The return from the close of the bar ending `start`'s window to the close of the bar
    `span.bars` further on — begins exactly where `start`'s window ended, sharing an anchor PRICE
    with it but never a bar's own return, so predictor and target windows never overlap."""
    begin, end = start.bars, start.bars + span.bars
    if begin < 1 or end > len(returns):
        return None
    return _compound(returns[begin:end])


def late_session_return(returns: Sequence[Decimal], start: Horizon) -> Decimal | None:
    """The return from the close of the bar ending `start`'s window to the session's final
    close — "late-session return", distinct from a fixed-length target horizon."""
    if start.bars < 1 or start.bars >= len(returns):
        return None
    return _compound(returns[start.bars :])
