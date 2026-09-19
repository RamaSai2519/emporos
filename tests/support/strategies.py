"""Doubles for the strategy framework: a scripted strategy, feeds, sinks and context builders.
Every one implements the same Protocol as the production collaborator it stands in for."""

from __future__ import annotations

import logging
import random
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.domain.ticks import Tick
from emporos.strategies.base import Strategy
from emporos.strategies.config import (
    ExecutionSettings,
    ResolvedStrategyConfig,
    RiskSettings,
    SessionSettings,
    StrategyParameters,
    UniverseMember,
)
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import ClosedBarHistory
from emporos.strategies.positions import FlatPositions, PositionView
from emporos.strategies.runner import MarketEvent, ReplayClockSync, StrategyRunner
from tests.support.fakes import RecordingAlertSink

INSTRUMENT = "NSE:1001"
OTHER_INSTRUMENT = "NSE:1002"
RUN_ID = "run-test-1"
T0 = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)  # 09:15 IST, the session open


def bar_at(
    instrument_id: str = INSTRUMENT,
    minutes: int = 0,
    close: str = "100",
    timeframe: Timeframe = Timeframe.M5,
) -> Candle:
    """A bar opening `minutes` after the session open."""
    price = Money.of(close)
    return Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        ts=T0 + timedelta(minutes=minutes),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=100,
    )


def tick_at(instrument_id: str = INSTRUMENT, seconds: int = 0, ltp: str = "100") -> Tick:
    when = T0 + timedelta(seconds=seconds)
    return Tick(instrument_id, when, when, Money.of(ltp), seconds + 1)


def make_signal(
    run_id: str = RUN_ID,
    instrument_id: str = INSTRUMENT,
    kind: SignalKind = SignalKind.ENTRY,
    side: OrderSide = OrderSide.BUY,
    quantity: int = 10,
    price: str = "100",
    ts: datetime = T0,
    reason: str = "test",
) -> Signal:
    return Signal(
        strategy_run_id=run_id,
        instrument_id=instrument_id,
        kind=kind,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        limit_price=Money.of(price),
        ts=ts,
        reason=reason,
    )


def make_config(
    name: str = "scripted",
    instruments: tuple[str, ...] = (INSTRUMENT,),
    parameters: StrategyParameters | None = None,
    timeframe: Timeframe = Timeframe.M5,
) -> ResolvedStrategyConfig:
    return ResolvedStrategyConfig(
        name=name,
        enabled=True,
        timeframe=timeframe,
        universe=tuple(UniverseMember(symbol=f"NSE:SYM{i}", instrument_id=i) for i in instruments),
        parameters=parameters or StrategyParameters(),
        risk=RiskSettings(
            max_position_value=Decimal(50000),
            max_open_positions=3,
            stop_loss_pct=Decimal(1),
            target_pct=Decimal(2),
        ),
        execution=ExecutionSettings(
            limit_buffer_bps=Decimal(5), reprice_after_seconds=30, max_reprices=3
        ),
        session=SessionSettings.model_validate(
            {"no_new_entries_after": "15:00", "square_off_at": "15:15"}
        ),
    )


def make_context(
    clock: FixedClock,
    history: ClosedBarHistory | None = None,
    positions: PositionView | None = None,
    config: ResolvedStrategyConfig | None = None,
    run_id: str = RUN_ID,
) -> StrategyContext:
    return StrategyContext(
        run_id=run_id,
        config=config or make_config(),
        clock=clock,
        logger=logging.getLogger("test.strategy"),
        history=history or ClosedBarHistory(clock),
        positions=positions or FlatPositions(),
        rng=random.Random(0),
    )


class ScriptedStrategy(Strategy):
    """Records every call; queues whatever `on_data` returns; raises where told to."""

    name = "scripted"
    parameters_model = StrategyParameters

    def __init__(
        self,
        config: ResolvedStrategyConfig,
        on_data: Callable[[MarketEvent, StrategyContext], Iterable[Signal]] | None = None,
        fail_in: str | None = None,
        forever: Signal | None = None,
    ) -> None:
        super().__init__(config)
        self._on_data = on_data
        self._fail_in = fail_in
        self._forever = forever
        self._outbox: list[Signal] = []
        self._ctx: StrategyContext | None = None
        self.calls: list[str] = []
        self.events: list[MarketEvent] = []
        self.updates: list[OrderUpdate] = []
        self.queue_on_session_end: list[Signal] = []

    def _maybe_fail(self, where: str) -> None:
        self.calls.append(where)
        if self._fail_in == where:
            raise RuntimeError(f"boom in {where}")

    def initialize(self, ctx: StrategyContext) -> None:
        self._ctx = ctx
        self._maybe_fail("initialize")

    def on_market_data(self, event: MarketEvent) -> None:
        self._maybe_fail("on_market_data")
        self.events.append(event)
        assert self._ctx is not None
        if self._on_data is not None:
            self._outbox.extend(self._on_data(event, self._ctx))

    def generate_signal(self) -> Signal | None:
        self._maybe_fail("generate_signal")
        if self._forever is not None:
            return self._forever
        return self._outbox.pop(0) if self._outbox else None

    def on_order_update(self, update: OrderUpdate) -> None:
        self._maybe_fail("on_order_update")
        self.updates.append(update)

    def on_session_end(self) -> None:
        self._maybe_fail("on_session_end")
        self._outbox.extend(self.queue_on_session_end)

    def on_shutdown(self) -> None:
        self._maybe_fail("on_shutdown")


class ListSignalSink:
    """`SignalSink` that keeps what it is given, and can be told to fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.signals: list[Signal] = []
        self._error = error

    async def submit(self, signal: Signal) -> None:
        if self._error is not None:
            raise self._error
        self.signals.append(signal)


class ListFeed:
    """A finished feed over a fixed list of events."""

    def __init__(self, events: Iterable[MarketEvent]) -> None:
        self._events = list(events)

    def __aiter__(self) -> AsyncIterator[MarketEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[MarketEvent]:
        for event in self._events:
            yield event


class RunnerRig:
    """One strategy wired to a replay clock, history, a sink and alerts."""

    def __init__(
        self,
        strategy: ScriptedStrategy | None = None,
        positions: PositionView | None = None,
        sink: ListSignalSink | None = None,
        config: ResolvedStrategyConfig | None = None,
    ) -> None:
        self.config = config or make_config()
        self.clock = FixedClock(T0)
        self.history = ClosedBarHistory(self.clock)
        self.alerts = RecordingAlertSink()
        self.sink = sink or ListSignalSink()
        self.strategy = strategy or ScriptedStrategy(self.config)
        self.context = make_context(self.clock, self.history, positions, self.config)
        self.runner = StrategyRunner(
            self.strategy, self.context, self.history, self.sink, ReplayClockSync(self.clock),
            self.alerts,
        )  # fmt: skip
