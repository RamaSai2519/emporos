"""1-minute candle aggregation on the wall clock (EM-52, plan.md §7).

Bars are built in memory from normalized ticks and closed by **time**, not by the arrival of the
next tick — otherwise an illiquid instrument's bar would never close. A bar for minute M closes
at M+1min plus a small `grace` (default 1s) that absorbs feed latency, so a tick stamped 10:00:59.8
that arrives at 10:01:00.2 still lands in the 10:00 bar. Closing is still fully deterministic.

Rules, each guarded by a test:

* A closed candle is NEVER mutated. A tick for an already-closed minute is counted and dropped.
* An out-of-order tick inside the still-open bar may widen its high/low, but can never change its
  open or close (it is older than the tick that set them).
* A silent instrument gets a flat, zero-volume bar (OHLC = previous close) on schedule, once it has
  a known price that session. Nothing is fabricated across days or outside 09:15-15:30 IST.
* Volume is the change in the feed's cumulative day volume. A bar with no volume baseline (feed
  subscribed mid-session) is flagged `partial`; so is any bar overlapping a feed disconnection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from emporos.core.clock import IST, Clock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.ticks import Tick
from emporos.marketdata.session import SessionWindow

_LOG = logging.getLogger(__name__)
_MINUTE = timedelta(minutes=1)
DEFAULT_GRACE = timedelta(seconds=1)
_MAX_REMEMBERED_GAPS = 200


class CandleSubscriber(Protocol):
    def on_candle(self, candle: Candle) -> None: ...


def floor_minute(moment: datetime) -> datetime:
    """IST's offset is a whole number of minutes, so UTC and IST minutes coincide."""
    return moment.replace(second=0, microsecond=0)


@dataclass
class _OpenBar:
    start: datetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume: int = 0
    partial: bool = False


@dataclass
class _InstrumentState:
    instrument_id: str
    day: date
    next_start: datetime | None  # the earliest minute not yet closed; None once the day is done
    open_bar: _OpenBar | None = None
    last_close: Money | None = None
    last_cumulative_volume: int | None = None


@dataclass(frozen=True)
class AggregatorStats:
    candles_closed: int
    flat_bars: int
    late_ticks_dropped: int
    widened_by_late_tick: int


@dataclass
class _Gap:
    start: datetime
    end: datetime | None = None

    def overlaps(self, minute_start: datetime) -> bool:
        minute_end = minute_start + _MINUTE
        return self.start < minute_end and (self.end is None or minute_start < self.end)


@dataclass
class _Counters:
    closed: int = 0
    flat: int = 0
    late: int = 0
    widened: int = 0


class MinuteCandleAggregator:
    """A `TickSubscriber` and a `ConnectionListener`; emits closed 1m `Candle`s to subscribers."""

    def __init__(
        self,
        clock: Clock,
        window: SessionWindow | None = None,
        grace: timedelta = DEFAULT_GRACE,
    ) -> None:
        self._clock = clock
        self._window = window or SessionWindow()
        self._grace = grace
        self._states: dict[str, _InstrumentState] = {}
        self._subscribers: list[CandleSubscriber] = []
        self._gaps: list[_Gap] = []
        self._counters = _Counters()

    def subscribe(self, subscriber: CandleSubscriber) -> None:
        self._subscribers.append(subscriber)

    @property
    def grace(self) -> timedelta:
        return self._grace

    @property
    def stats(self) -> AggregatorStats:
        c = self._counters
        return AggregatorStats(c.closed, c.flat, c.late, c.widened)

    # -- ConnectionListener: remember when the feed was down ---------------------------------

    async def on_connected(self) -> None:
        if self._gaps and self._gaps[-1].end is None:
            self._gaps[-1].end = self._clock.now()

    async def on_disconnected(self, reason: str) -> None:
        if not self._gaps or self._gaps[-1].end is not None:
            self._gaps.append(_Gap(self._clock.now()))
            del self._gaps[:-_MAX_REMEMBERED_GAPS]
        # Volume traded during the outage would otherwise all land in the first bar after it:
        # forget the baseline so that bar re-baselines (and is flagged partial).
        for state in self._states.values():
            state.last_cumulative_volume = None

    # -- TickSubscriber ----------------------------------------------------------------------

    def on_tick(self, tick: Tick) -> None:
        start = floor_minute(tick.exchange_ts)
        day = start.astimezone(IST).date()
        state = self._states.get(tick.instrument_id)
        if state is not None and day > state.day:
            self._close_due(state, datetime.max.replace(tzinfo=start.tzinfo))  # finish yesterday
            state = None
        if state is None:
            state = _InstrumentState(tick.instrument_id, day=day, next_start=start)
            self._states[tick.instrument_id] = state
            if start == self._window.open_at(day):
                state.last_cumulative_volume = 0  # nothing traded before the open: full baseline
        if state.next_start is None or start < state.next_start:
            self._counters.late += 1  # its minute is already closed: never rewrite it
            return
        if state.open_bar is not None and state.open_bar.start == start:
            self._fold(state, tick)
            return
        if tick.out_of_order:  # older than the latest tick, yet its minute is not open
            self._counters.late += 1
            return
        self._close_due(state, start)
        state.open_bar = self._new_bar(state, tick, start)
        self._fold(state, tick, opening=True)

    # -- time -------------------------------------------------------------------------------

    def advance(self, now: datetime | None = None) -> None:
        """Close every bar whose minute (plus grace) has elapsed. Called on a wall-clock timer."""
        moment = now or self._clock.now()
        cutoff = floor_minute(moment - self._grace)
        for instrument_id, state in list(self._states.items()):
            self._close_due(state, cutoff)
            if state.next_start is None and moment.astimezone(IST).date() > state.day:
                del self._states[instrument_id]  # the day is done; free the state

    # -- internals ---------------------------------------------------------------------------

    def _new_bar(self, state: _InstrumentState, tick: Tick, start: datetime) -> _OpenBar:
        no_baseline = state.last_cumulative_volume is None and tick.volume is not None
        return _OpenBar(start, tick.ltp, tick.ltp, tick.ltp, tick.ltp, partial=no_baseline)

    def _fold(self, state: _InstrumentState, tick: Tick, opening: bool = False) -> None:
        bar = state.open_bar
        assert bar is not None
        if tick.out_of_order and not opening:
            if tick.ltp > bar.high or tick.ltp < bar.low:
                bar.high, bar.low = max(bar.high, tick.ltp), min(bar.low, tick.ltp)
                self._counters.widened += 1
            return  # open/close/volume belong to the in-order ticks
        bar.high, bar.low, bar.close = max(bar.high, tick.ltp), min(bar.low, tick.ltp), tick.ltp
        if tick.volume is not None:
            baseline = state.last_cumulative_volume
            if baseline is not None and tick.volume > baseline:
                bar.volume += tick.volume - baseline
            state.last_cumulative_volume = tick.volume

    def _close_due(self, state: _InstrumentState, cutoff_start: datetime) -> None:
        """Close every unclosed minute that starts before `cutoff_start`."""
        while state.next_start is not None and state.next_start < cutoff_start:
            if state.open_bar is None and state.last_close is None:
                state.next_start = self._following(state.next_start)
                continue
            self._emit(state, state.next_start)
            state.next_start = self._following(state.next_start)

    def _emit(self, state: _InstrumentState, start: datetime) -> None:
        bar = state.open_bar if state.open_bar and state.open_bar.start == start else None
        if bar is None:
            assert state.last_close is not None
            price = state.last_close
            bar = _OpenBar(start, price, price, price, price)  # silent: flat, zero volume
            self._counters.flat += 1
        else:
            state.open_bar = None
        state.last_close = bar.close
        candle = Candle(
            instrument_id=state.instrument_id,
            timeframe=Timeframe.M1,
            ts=start,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            partial=bar.partial or any(gap.overlaps(start) for gap in self._gaps),
        )
        self._counters.closed += 1
        for subscriber in self._subscribers:
            self._deliver(subscriber, candle)

    def _following(self, start: datetime) -> datetime | None:
        following = start + _MINUTE
        return following if self._window.contains(following) else None

    @staticmethod
    def _deliver(subscriber: CandleSubscriber, candle: Candle) -> None:
        try:
            subscriber.on_candle(candle)
        except Exception:  # one broken consumer must not stop candles reaching the others
            _LOG.exception("candle subscriber %s failed", type(subscriber).__name__)
