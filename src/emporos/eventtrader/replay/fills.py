"""Entry and exit fills for the replay (EM-240), on the program's own intraday fill rules.

An entry is a marketable limit a buffer through the last completed bar's close, tried on the
FIRST eligible 5-minute bar only: the bar must trade strictly through the limit and have room for
the order in 10% of its volume, else there is no trade (no chasing). Exits: the stop, the target,
and for an intraday position the 15:15 square-off. Prices are reference prices BEFORE slippage;
the cost model charges slippage and fees per scenario, as the screener does."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.eventtrader.replay.market import DailyBar, MarketData
from emporos.eventtrader.stages.models import Side
from emporos.research.scans.base import ScanExecution

__all__ = [
    "EntryFill",
    "EntryFiller",
    "ExitFill",
    "ExitReason",
    "ExitSimulator",
    "NoEntry",
    "entry_time",
    "price_level",
    "touch",
]

AFTER_HOURS_FIRST_ENTRY = time(9, 20)  # skips the opening print
LAST_ENTRY = time(15, 25)  # a decision after this waits for the next session
SESSION_END = time(15, 30)
TICK = Decimal("0.05")
_HUNDRED = Decimal(100)


class ExitReason(StrEnum):
    STOP = "stop"
    TARGET = "target"
    SQUARE_OFF = "square_off"
    TIME = "time"
    END_OF_DATA = "end_of_data"
    KILL = "kill"


class NoEntry(StrEnum):
    NO_BARS = "no_bars"
    NOT_THROUGH = "not_through"
    NO_VOLUME = "no_volume"
    NO_ANCHOR = "no_anchor"


@dataclass(frozen=True)
class EntryFill:
    ts: datetime  # the bar's start (UTC)
    day: date
    reference: Decimal  # the anchor close the limit was set from: the entry price before slippage
    bar_index: int  # position in that session's bars


@dataclass(frozen=True)
class ExitFill:
    ts: datetime  # UTC
    price: Decimal
    reason: ExitReason


def _ist(moment: datetime) -> datetime:
    return moment.astimezone(IST)


def entry_time(decision_at: datetime, market: MarketData) -> datetime | None:
    """When the entry order is placed. In session and by 15:25: the decision itself. After that, or
    before the open or on a non-session day: the next session's 09:20 (the opening print avoided).
    None when there is no later session in the data."""
    local = _ist(decision_at)
    today = local.date()
    if market.is_session(today) and time(9, 15) <= local.time() < LAST_ENTRY:
        return decision_at
    if market.is_session(today) and local.time() < time(9, 15):
        return datetime.combine(today, AFTER_HOURS_FIRST_ENTRY, tzinfo=IST)
    following = market.next_session(today)
    if following is None:
        return None
    return datetime.combine(following, AFTER_HOURS_FIRST_ENTRY, tzinfo=IST)


class EntryFiller:
    def __init__(self, market: MarketData, execution: ScanExecution) -> None:
        self._market, self._execution = market, execution

    def fill(
        self, instrument_id: str, side: Side, placed_at: datetime, quantity: int
    ) -> EntryFill | NoEntry:
        """Try the first bar that starts at or after `placed_at`, with the last bar closed by then
        as the anchor."""
        day = _ist(placed_at).date()
        bars = self._market.five_minute_bars(instrument_id, day)
        anchor = next((b for b in reversed(bars) if b.closes_at <= placed_at), None)
        first = next(((i, b) for i, b in enumerate(bars) if b.ts >= placed_at), None)
        if not bars or first is None:
            return NoEntry.NO_BARS
        if anchor is None:
            return NoEntry.NO_ANCHOR
        index, bar = first
        reference = anchor.close.amount
        buying = side is Side.LONG
        limit = self._execution.limit_for(OrderSide.BUY if buying else OrderSide.SELL, reference)
        through = bar.low.amount < limit if buying else bar.high.amount > limit
        if not through:
            return NoEntry.NOT_THROUGH
        if quantity > int(bar.volume * self._execution.participation):
            return NoEntry.NO_VOLUME
        return EntryFill(bar.ts, day, reference, index)


class ExitSimulator:
    """Walks a filled position forward to its exit."""

    def __init__(self, market: MarketData, execution: ScanExecution) -> None:
        self._market, self._execution = market, execution

    def intraday_exit(
        self, instrument_id: str, side: Side, entry: EntryFill, stop: Decimal, target: Decimal
    ) -> ExitFill:
        bars = self._market.five_minute_bars(instrument_id, entry.day)
        hit = self._scan_bars(
            bars[entry.bar_index + 1 :], side, stop, target, self._execution.square_off_at
        )
        if hit is not None:
            return hit
        return self._square_off(bars)

    def swing_exit(
        self,
        instrument_id: str,
        side: Side,
        entry: EntryFill,
        stop: Decimal,
        target: Decimal,
        hold_days: int,
    ) -> ExitFill:
        """The entry day's remaining 5-minute bars first, then daily bars: a bar touching both is
        the stop; a bar that opens beyond a level exits at its open; else the close of the session
        `hold_days` after entry."""
        bars = self._market.five_minute_bars(instrument_id, entry.day)
        hit = self._scan_bars(bars[entry.bar_index + 1 :], side, stop, target, SESSION_END)
        if hit is not None:
            return hit
        daily = self._market.daily_bars(instrument_id, entry.day, hold_days)
        for bar in daily:
            out = self._daily_hit(bar, side, stop, target)
            if out is not None:
                return out
        if len(daily) < hold_days or not daily:
            last = daily[-1] if daily else None
            when = _daily_close(last.day) if last else bars[-1].closes_at
            price = last.close if last else bars[-1].close.amount
            return ExitFill(when, price, ExitReason.END_OF_DATA)
        return ExitFill(_daily_close(daily[-1].day), daily[-1].close, ExitReason.TIME)

    # --- the pieces ----------------------------------------------------------------------------
    def _scan_bars(
        self, bars: Sequence[Candle], side: Side, stop: Decimal, target: Decimal, until: time
    ) -> ExitFill | None:
        for bar in bars:
            if _ist(bar.closes_at).time() > until:
                return None
            hit = touch(bar.open.amount, bar.high.amount, bar.low.amount, side, stop, target)
            if hit is not None:
                price, reason = hit
                return ExitFill(bar.closes_at, price, reason)
        return None

    def _daily_hit(
        self, bar: DailyBar, side: Side, stop: Decimal, target: Decimal
    ) -> ExitFill | None:
        hit = touch(bar.open, bar.high, bar.low, side, stop, target)
        if hit is None:
            return None
        price, reason = hit
        return ExitFill(_daily_close(bar.day), price, reason)

    def _square_off(self, bars: Sequence[Candle]) -> ExitFill:
        """The close of the last bar that completes by the square-off time."""
        cutoff = self._execution.square_off_at
        eligible = [b for b in bars if _ist(b.closes_at).time() <= cutoff]
        last = eligible[-1] if eligible else bars[-1]
        return ExitFill(last.closes_at, last.close.amount, ExitReason.SQUARE_OFF)


def price_level(reference: Decimal, side: Side, pct: float, *, against: bool) -> Decimal:
    """The stop (`against`) or the target, `pct` percent from the reference, on the 0.05 tick."""
    move = reference * Decimal(str(pct)) / _HUNDRED
    below = (side is Side.LONG) == against
    raw = reference - move if below else reference + move
    return (raw / TICK).quantize(Decimal(1), rounding=ROUND_HALF_UP) * TICK


def touch(
    open_: Decimal, high: Decimal, low: Decimal, side: Side, stop: Decimal, target: Decimal
) -> tuple[Decimal, ExitReason] | None:
    """The stop first when a bar reaches both; a bar opening past a level fills at its open. The
    side is the direction of the POSITION (or, for an option, of the underlying view)."""
    if side is Side.LONG:
        if low <= stop:
            return min(open_, stop), ExitReason.STOP
        if high >= target:
            return max(open_, target), ExitReason.TARGET
    else:
        if high >= stop:
            return max(open_, stop), ExitReason.STOP
        if low <= target:
            return min(open_, target), ExitReason.TARGET
    return None


def _daily_close(day: date) -> datetime:
    return datetime.combine(day, SESSION_END, tzinfo=IST)
