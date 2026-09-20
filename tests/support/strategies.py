"""Doubles for the strategy framework: a scripted strategy, feeds, sinks and context builders.
Every one implements the same Protocol as the production collaborator it stands in for."""

from __future__ import annotations

import copy
import logging
import random
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from emporos.cli.strategy_composition import build_registry
from emporos.core.clock import Clock, FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.positions import Position
from emporos.domain.signals import Signal, SignalKind
from emporos.domain.ticks import Tick
from emporos.instruments.cache import InstrumentCache
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import SignalRecord, StrategyRecord, StrategyRunRecord
from emporos.session.strategy_runs import RunEnvironment, StartedRun, StrategyRunnerBuilder
from emporos.strategies.base import Strategy
from emporos.strategies.config import (
    ExactDecimal,
    ExecutionSettings,
    PositiveInt,
    ResolvedStrategyConfig,
    RiskSettings,
    SessionSettings,
    StrategyParameters,
    UniverseMember,
)
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import ClosedBarHistory
from emporos.strategies.positions import FlatPositions, PositionView
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.resolution import StrategyConfigResolver
from emporos.strategies.runner import MarketEvent, ReplayClockSync, RunReport, StrategyRunner
from emporos.strategies.snapshot import ConfigSnapshotter
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
    clock: Clock,
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


class ThresholdParameters(StrategyParameters):
    threshold: ExactDecimal
    quantity: PositiveInt = 1


class ThresholdStrategy(Strategy):
    """Buys whenever a closed bar's close is above a configured threshold. Its signals depend on
    its parameters, so a run only reproduces if the SAME parameters come back."""

    name = "threshold"
    parameters_model = ThresholdParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        super().__init__(config)
        assert isinstance(config.parameters, ThresholdParameters)
        self._params = config.parameters
        self._outbox: list[Signal] = []
        self._ctx: StrategyContext | None = None

    def initialize(self, ctx: StrategyContext) -> None:
        self._ctx = ctx

    def on_market_data(self, event: MarketEvent) -> None:
        assert self._ctx is not None
        if isinstance(event, Candle) and event.close.amount > self._params.threshold:
            self._outbox.append(
                Signal(
                    strategy_run_id=self._ctx.run_id,
                    instrument_id=event.instrument_id,
                    kind=SignalKind.ENTRY,
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=self._params.quantity,
                    limit_price=event.close,
                    ts=event.closes_at,
                    reason=f"close above {self._params.threshold}",
                )
            )

    def generate_signal(self) -> Signal | None:
        return self._outbox.pop(0) if self._outbox else None


INSTRUMENT_MASTER = InstrumentCache(
    [
        Instrument(Exchange.NSE, "1001", "ALPHA-EQ", "Alpha Ltd", 1, Money.of("0.05")),
        Instrument(Exchange.NSE, "1002", "BETA-EQ", "Beta Ltd", 1, Money.of("0.05")),
    ]
)


def raw_config(name: str = "threshold") -> dict[str, Any]:
    """A valid strategy file as a parsed YAML mapping (what `yaml.safe_load` would return)."""
    return {
        "name": name,
        "enabled": True,
        "timeframe": "5m",
        "universe": {"type": "static", "instruments": ["NSE:ALPHA-EQ", "NSE:BETA-EQ"]},
        "parameters": {"threshold": "100", "quantity": 10},
        "risk": {
            "max_position_value": 50000,
            "max_open_positions": 3,
            "stop_loss_pct": "1.0",
            "target_pct": "2.0",
        },
        "execution": {"limit_buffer_bps": 5, "reprice_after_seconds": 30, "max_reprices": 3},
        "session": {"no_new_entries_after": "15:00", "square_off_at": "15:15"},
    }


def changed(raw: dict[str, Any], path: str, value: object) -> dict[str, Any]:
    """A deep copy of `raw` with the dotted `path` set to `value` (`None` removes the key)."""
    result = copy.deepcopy(raw)
    *parents, leaf = path.split(".")
    node = result
    for part in parents:
        node = node[part]
    if value is _REMOVE:
        del node[leaf]
    else:
        node[leaf] = value
    return result


_REMOVE = object()
REMOVE = _REMOVE


def threshold_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(ThresholdStrategy)
    return registry


class InMemoryStrategyStore:
    """`StrategyStore` double; enforces the unique name index like the real collection."""

    def __init__(self, race_winner: StrategyRecord | None = None) -> None:
        self.records: dict[str, StrategyRecord] = {}
        self._race_winner = race_winner

    async def get_by_name(self, name: str) -> StrategyRecord | None:
        return next((r for r in self.records.values() if r.name == name), None)

    async def insert(self, record: StrategyRecord) -> None:
        if self._race_winner is not None:  # someone else registered the name a moment ago
            self.records[self._race_winner.id] = self._race_winner
            self._race_winner = None
        if any(r.name == record.name for r in self.records.values()):
            raise DuplicateRecordError("strategies", "name_1", ("name",))
        self.records[record.id] = record

    async def replace(self, record: StrategyRecord) -> None:
        self.records[record.id] = record


class InMemoryRunStore:
    def __init__(self) -> None:
        self.records: dict[str, StrategyRunRecord] = {}

    async def insert(self, record: StrategyRunRecord) -> None:
        self.records[record.id] = record

    async def get(self, record_id: str) -> StrategyRunRecord | None:
        return self.records.get(record_id)


class InMemorySignalStore:
    def __init__(self) -> None:
        self.records: list[SignalRecord] = []

    async def insert(self, record: SignalRecord) -> None:
        self.records.append(record)


# --- momentum_v1 and replay helpers -----------------------------------------------------------


def closes_to_bars(
    closes: Iterable[str],
    instrument_id: str = INSTRUMENT,
    start: datetime = T0,
    timeframe: Timeframe = Timeframe.M5,
) -> list[Candle]:
    """One bar per close, back to back from `start`; each opens at the previous close."""
    bars: list[Candle] = []
    previous: Money | None = None
    for index, close in enumerate(closes):
        price = Money.of(close)
        opened = previous or price
        bars.append(
            Candle(
                instrument_id=instrument_id,
                timeframe=timeframe,
                ts=start + timeframe.duration * index,
                open=opened,
                high=max(opened, price),
                low=min(opened, price),
                close=price,
                volume=1000,
            )
        )
        previous = price
    return bars


def wave_closes(count: int, period: int = 60, amplitude: int = 40, base: int = 1000) -> list[str]:
    """A deterministic price path: a triangle wave with integer noise from a fixed-seed LCG, so
    fast and slow averages cross repeatedly. Same arguments, same series, on any machine."""
    state = 7
    closes: list[str] = []
    for index in range(count):
        state = (state * 1103515245 + 12345) % 2**31
        noise = (state >> 16) % 7 - 3
        phase = index % period
        triangle = phase if phase <= period // 2 else period - phase
        closes.append(str(base + (triangle * amplitude * 2) // period + noise))
    return closes


def momentum_raw(**parameters: Any) -> dict[str, Any]:
    """`momentum_v1` on the two test instruments, with small periods a person can check by hand."""
    raw = raw_config(name="momentum_v1")
    raw["parameters"] = {
        "fast_ema": 2,
        "slow_ema": 3,
        "rsi_period": 2,
        "rsi_entry_min": 50,
        "rsi_entry_max": 100,
    } | parameters
    return raw


class BookPositions:
    """A `PositionView` a test can set. Implements the same Protocol as the portfolio's view."""

    def __init__(self) -> None:
        self._held: dict[str, Position] = {}

    def set(self, instrument_id: str, quantity: int, price: str = "100") -> None:
        self._held[instrument_id] = Position(instrument_id, quantity, Money.of(price))

    def position(self, instrument_id: str) -> Position:
        return self._held.get(instrument_id) or Position.flat(instrument_id)

    def open_positions(self) -> tuple[Position, ...]:
        return tuple(p for p in self._held.values() if not p.is_flat)


class FillingSink:
    """A `SignalSink` that pretends every signal fills instantly at its limit price, so a replay
    can see a strategy exit what it entered. A test device, not a fill model."""

    def __init__(self, book: BookPositions, inner: ListSignalSink) -> None:
        self._book = book
        self._inner = inner

    async def submit(self, signal: Signal) -> None:
        held = self._book.position(signal.instrument_id).net_quantity
        change = signal.quantity if signal.side is OrderSide.BUY else -signal.quantity
        self._book.set(signal.instrument_id, held + change, str(signal.limit_price.amount))
        await self._inner.submit(signal)


@dataclass(frozen=True)
class ReplayResult:
    signals: list[Signal]
    report: RunReport
    alerts: list[tuple[str, str]]


async def replay(
    config: ResolvedStrategyConfig,
    bars: Iterable[Candle],
    *,
    fills: bool = False,
    positions: BookPositions | None = None,
    warmup: Iterable[Candle] = (),
    warmup_clock: datetime | None = None,
    run_id: str = RUN_ID,
) -> ReplayResult:
    """Run one strategy over `bars` on a replay clock. `warmup` bars are recorded into history
    before the runner starts, as a mid-day start would; the clock moves past the last of them
    unless `warmup_clock` says where it stands (bars beyond it are held but not yet readable)."""
    registry = build_registry()
    started = StartedRun(run_id, config, ConfigSnapshotter().take(config), "2026-01-05")
    book = positions or BookPositions()
    clock = FixedClock(T0)
    collected = ListSignalSink()
    sink: ListSignalSink | FillingSink = FillingSink(book, collected) if fills else collected
    alerts = RecordingAlertSink()
    prepared = StrategyRunnerBuilder(registry).build(
        started, RunEnvironment(clock, ReplayClockSync(clock), sink, book, alerts)
    )
    warmed = list(warmup)
    for bar in warmed:
        prepared.history.record(bar)
    if warmup_clock is not None:
        clock.set(warmup_clock)
    elif warmed:
        clock.set(max(bar.closes_at for bar in warmed))
    report = await prepared.runner.run(ListFeed(bars))
    return ReplayResult(collected.signals, report, alerts.alerts)


def resolved(raw: dict[str, Any]) -> ResolvedStrategyConfig:
    return StrategyConfigResolver(build_registry(), INSTRUMENT_MASTER).resolve(raw)


def session_bars(
    closes: Sequence[str], instrument_id: str, first_day: datetime = T0, per_day: int = 75
) -> list[Candle]:
    """`closes` laid out as consecutive 5m sessions: `per_day` bars from 09:15 IST, then the
    next weekday-agnostic day (calendar days; the strategy does not care about weekends)."""
    bars: list[Candle] = []
    for day, offset in enumerate(range(0, len(closes), per_day)):
        chunk = closes[offset : offset + per_day]
        bars += closes_to_bars(chunk, instrument_id, first_day + timedelta(days=day))
    return bars
