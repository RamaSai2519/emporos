"""Doubles and rigs for the backtest package. Each implements the same Protocol as the production
collaborator it stands in for (`CandleReader`, `SessionObserver`)."""

from __future__ import annotations

import gc
import logging
import types
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.clock import BarClock
from emporos.backtest.orders import SimEvent, SimOrderRequest
from emporos.backtest.replay import BarReplay, SessionObserver
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal
from emporos.strategies.history import ClosedBarHistory
from emporos.strategies.runner import ClockSync, MarketEvent, ReplayClockSync, StrategyRunner
from tests.support.fakes import RecordingAlertSink
from tests.support.strategies import (
    INSTRUMENT,
    T0,
    ListSignalSink,
    ScriptedStrategy,
    closes_to_bars,
    make_config,
    make_context,
)


class InMemoryCandles:
    """`CandleReader` over a fixed list. Records every read so a test can see what was asked for."""

    def __init__(self, bars: Iterable[Candle]) -> None:
        self._bars = list(bars)
        self.reads: list[tuple[str, Timeframe, datetime, datetime]] = []

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.reads.append((instrument_id, timeframe, start, end))
        found = [
            bar
            for bar in self._bars
            if bar.instrument_id == instrument_id
            and bar.timeframe is timeframe
            and start <= bar.ts < end
        ]
        return sorted(found, key=lambda bar: bar.ts)


def trading_days(
    days: int,
    closes_per_day: Sequence[str],
    instrument_id: str = INSTRUMENT,
    timeframe: Timeframe = Timeframe.M5,
    first_day: datetime = T0,
) -> list[Candle]:
    """`days` consecutive sessions, each opening at 09:15 IST and made of `closes_per_day`."""
    bars: list[Candle] = []
    for day in range(days):
        bars += closes_to_bars(
            closes_per_day, instrument_id, first_day + timedelta(days=day), timeframe
        )
    return bars


class RecordingObserver:
    """A `SessionObserver` that writes down what it was told, and where the clock stood."""

    def __init__(self, clock: BarClock) -> None:
        self._clock = clock
        self.log: list[tuple[str, object, datetime]] = []

    async def before_bar(self, bar: Candle) -> None:
        self.log.append(("bar", bar.ts, self._clock.now()))

    async def session_ending(self, day: date) -> None:
        self.log.append(("ending", day, self._clock.now()))

    async def session_closed(self, day: date) -> None:
        self.log.append(("session", day, self._clock.now()))


class ReplayRig:
    """One scripted strategy on the real runner, history and replay loop, on a `BarClock`."""

    def __init__(
        self,
        on_data: Callable[[MarketEvent, Any], Iterable[Signal]] | None = None,
        observer: SessionObserver | None = None,
        strategy: ScriptedStrategy | None = None,
        timeframe: Timeframe = Timeframe.M5,
        clock_sync: ClockSync | None = None,
    ) -> None:
        self.config = make_config(timeframe=timeframe)
        self.clock = BarClock(T0)
        self.observer = RecordingObserver(self.clock)
        self.history = ClosedBarHistory(self.clock.view())
        self.alerts = RecordingAlertSink()
        self.sink = ListSignalSink()
        self.strategy = strategy or ScriptedStrategy(self.config, on_data=on_data)
        self.context = make_context(self.clock.view(), self.history, config=self.config)
        self.runner = StrategyRunner(
            self.strategy, self.context, self.history, self.sink,
            clock_sync or ReplayClockSync(self.clock), self.alerts,
        )  # fmt: skip
        self.replay = BarReplay(self.runner, self.clock, observer or self.observer)


_SKIPPED = (types.ModuleType, types.FunctionType, types.MethodType, type, logging.Logger)


def reachable_candles(root: object) -> list[Candle]:
    """Every `Candle` a holder of `root` could get to by following references (what reflection
    could find). Modules, types, functions and loggers are not followed: they lead to the whole
    interpreter, not to anything the object was given."""
    seen: set[int] = set()
    pending: list[object] = [root]
    found: list[Candle] = []
    while pending:
        item = pending.pop()
        if id(item) in seen or isinstance(item, _SKIPPED):
            continue
        seen.add(id(item))
        if isinstance(item, Candle):
            found.append(item)
        pending.extend(gc.get_referents(item))
    return found


def ohlc(
    open_: str,
    high: str,
    low: str,
    close: str,
    minutes: int = 0,
    volume: int = 1000,
    instrument_id: str = INSTRUMENT,
    partial: bool = False,
    timeframe: Timeframe = Timeframe.M5,
) -> Candle:
    """One bar opening `minutes` after the session open, with its full range."""
    return Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        ts=T0 + timedelta(minutes=minutes),
        open=Money.of(open_),
        high=Money.of(high),
        low=Money.of(low),
        close=Money.of(close),
        volume=volume,
        partial=partial,
    )


def limit_order(
    side: OrderSide = OrderSide.BUY,
    price: str = "100",
    quantity: int = 10,
    tag: str = "t1",
    instrument_id: str = INSTRUMENT,
    strategy_run_id: str = "run-1",
) -> SimOrderRequest:
    return SimOrderRequest(
        instrument_id, side, OrderType.LIMIT, quantity, Money.of(price), tag, strategy_run_id
    )


def stop_order(
    side: OrderSide = OrderSide.SELL,
    trigger: str = "98",
    limit: str = "97",
    quantity: int = 10,
    tag: str = "s1",
    strategy_run_id: str = "run-1",
) -> SimOrderRequest:
    return SimOrderRequest(
        INSTRUMENT,
        side,
        OrderType.STOPLOSS_LIMIT,
        quantity,
        Money.of(limit),
        tag,
        strategy_run_id,
        Money.of(trigger),
    )


class BrokerRig:
    """A broker on a bar clock, stepped bar by bar the way the engine steps it: the clock moves to
    the bar's close, the broker matches the bar, THEN the strategy (here: the test) may order."""

    def __init__(self, model=None, rejects=None) -> None:  # type: ignore[no-untyped-def]
        self.clock = BarClock(T0)
        self.broker = SimulatedBroker(self.clock, model, rejects)

    def step(self, bar: Candle) -> list[SimEvent]:
        self.clock.set(bar.closes_at)
        return self.broker.match(bar)
