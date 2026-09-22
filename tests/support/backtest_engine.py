"""Rigs for the engine tests: a strategy whose trades a person can predict, a fixed fee schedule,
and helpers that lay out sessions of bars with known prices."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

from emporos.backtest.costs import ScheduleSource
from emporos.backtest.engine import BacktestEngine, BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.backtest.pricing import GateContext, GateRejection, SignalGate
from emporos.backtest.progress import BacktestProgressSink
from emporos.backtest.settings import FillSettings
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.strategies.base import Strategy
from emporos.strategies.config import (
    NonNegativeInt,
    PositiveInt,
    ResolvedStrategyConfig,
    SessionSettings,
    StrategyParameters,
)
from emporos.strategies.context import StrategyContext
from emporos.strategies.registry import StrategyRegistry
from tests.support.backtest import InMemoryCandles
from tests.support.strategies import INSTRUMENT, T0, make_config


class BuyThenSellParameters(StrategyParameters):
    buy_at: NonNegativeInt = 2  # buy on the close of this bar of the session's count (0 = never)
    sell_at: NonNegativeInt = 0
    quantity: PositiveInt = 10
    fail_at: NonNegativeInt = 0  # raise on this bar (0 = never)


class BuyThenSell(Strategy):
    """Counts the bars it is given (per run, across sessions) and trades on the chosen ones, with
    the bar's close as its limit. It reports what it was told, for tests to inspect."""

    name = "buy_then_sell"
    parameters_model = BuyThenSellParameters
    updates: ClassVar[list[OrderUpdate]] = []
    positions_seen_at_update: ClassVar[list[int]] = []
    history_at_start: ClassVar[list[int]] = []

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        super().__init__(config)
        assert isinstance(config.parameters, BuyThenSellParameters)
        self._p = config.parameters
        self._seen = 0
        self._outbox: deque[Signal] = deque()
        self._ctx: StrategyContext | None = None

    @classmethod
    def reset(cls) -> None:
        cls.updates.clear()
        cls.positions_seen_at_update.clear()
        cls.history_at_start.clear()

    def initialize(self, ctx: StrategyContext) -> None:
        self._ctx = ctx
        self.history_at_start.append(
            len(ctx.history.bars(INSTRUMENT, self._config.timeframe, 10_000))
        )

    def on_market_data(self, event: Candle | object) -> None:
        if not isinstance(event, Candle):
            return
        self._seen += 1
        if self._seen == self._p.fail_at:
            raise RuntimeError("the strategy blew up")
        if self._seen == self._p.buy_at:
            self._emit(event, SignalKind.ENTRY, OrderSide.BUY)
        elif self._seen == self._p.sell_at:
            self._emit(event, SignalKind.EXIT, OrderSide.SELL)

    def generate_signal(self) -> Signal | None:
        return self._outbox.popleft() if self._outbox else None

    def on_order_update(self, update: OrderUpdate) -> None:
        assert self._ctx is not None
        self.updates.append(update)
        self.positions_seen_at_update.append(
            self._ctx.positions.position(update.instrument_id).net_quantity
        )

    def _emit(self, bar: Candle, kind: SignalKind, side: OrderSide) -> None:
        assert self._ctx is not None
        self._outbox.append(
            Signal(
                self._ctx.run_id, bar.instrument_id, kind, side, OrderType.LIMIT,
                self._p.quantity, bar.close, bar.closes_at, f"{kind.value} on bar {self._seen}",
            )
        )  # fmt: skip


class FixedSchedule:
    """The same schedule on every day. The rates are the shipped Angel One intraday ones."""

    def __init__(self) -> None:
        self._schedule = FeeSchedule(
            name="hand",
            effective_from=date(2026, 1, 1),
            brokerage_flat=Money.of("20"),
            brokerage_percent=Decimal("0.1"),
            brokerage_minimum=Money.of("5"),
            stt_sell_percent=Decimal("0.025"),
            exchange_transaction_percent={Exchange.NSE: Decimal("0.0030699")},
            sebi_per_crore=Money.of("10"),
            stamp_duty_buy_percent=Decimal("0.003"),
            gst_percent=Decimal("18"),
        )

    def schedule_for(self, day: date) -> FeeSchedule:
        return self._schedule

    @property
    def assumed_days(self) -> frozenset[date]:
        return frozenset()


class FixedTicks:
    def tick_size(self, instrument_id: str) -> Money:
        return Money.of("0.05")


class RefuseAll:
    name = "refuse_all"

    def __init__(self, context: GateContext | None = None) -> None:
        self.context = context

    def review(self, signal: Signal) -> Signal | GateRejection:
        return GateRejection("test: nothing is allowed")


class HalveSize:
    """A gate that resizes: the order must carry ITS quantity, not the strategy's."""

    name = "halve_size"

    def __init__(self, context: GateContext | None = None) -> None:
        self.context = context

    def review(self, signal: Signal) -> Signal | GateRejection:
        return replace(signal, quantity=max(1, signal.quantity // 2))


def registry() -> StrategyRegistry:
    found = StrategyRegistry()
    found.register(BuyThenSell)
    return found


def config(
    buy_at: int = 2,
    sell_at: int = 0,
    fail_at: int = 0,
    square_off_at: str = "15:15",
    no_new_entries_after: str = "15:00",
    timeframe: Timeframe = Timeframe.M5,
) -> ResolvedStrategyConfig:
    base = make_config(
        name="buy_then_sell",
        parameters=BuyThenSellParameters(buy_at=buy_at, sell_at=sell_at, fail_at=fail_at),
        timeframe=timeframe,
    )
    session = SessionSettings.model_validate(
        {"no_new_entries_after": no_new_entries_after, "square_off_at": square_off_at}
    )
    return base.model_copy(update={"session": session})


def bars(
    rows: Sequence[tuple[str, str, str, str]],
    day: int = 0,
    volume: int = 10_000,
    instrument_id: str = INSTRUMENT,
    timeframe: Timeframe = Timeframe.M5,
) -> list[Candle]:
    """One session's bars from 09:15 IST, from (open, high, low, close) rows, on day `day`."""
    start = T0 + timedelta(days=day)
    return [
        Candle(
            instrument_id, timeframe, start + timeframe.duration * n,
            Money.of(o), Money.of(h), Money.of(low), Money.of(c), volume,
        )
        for n, (o, h, low, c) in enumerate(rows)
    ]  # fmt: skip


def spec(
    strategy: ResolvedStrategyConfig,
    days: int = 1,
    fills: FillSettings | None = None,
    start: datetime = T0 - timedelta(hours=1),
    warmup_bars: int = 0,
) -> BacktestSpec:
    return BacktestSpec(
        config=strategy,
        window=FeedWindow(start, T0 + timedelta(days=days) + timedelta(hours=10)),
        starting_cash=Money.of("100000"),
        fills=fills or FillSettings(),
        warmup_bars=warmup_bars,
    )


def engine(
    candles: Sequence[Candle],
    gate: type[SignalGate] | None = None,
    schedules: ScheduleSource | None = None,
    progress: BacktestProgressSink | None = None,
) -> BacktestEngine:
    source = schedules or FixedSchedule()
    reader = InMemoryCandles(candles)
    if gate is None:
        return BacktestEngine(reader, registry(), FixedTicks(), lambda: source, progress=progress)
    return BacktestEngine(reader, registry(), FixedTicks(), lambda: source, gate, progress=progress)


async def run(
    candles: Sequence[Candle],
    strategy: ResolvedStrategyConfig,
    days: int = 1,
    fills: FillSettings | None = None,
    gate: type[SignalGate] | None = None,
    progress: BacktestProgressSink | None = None,
) -> BacktestResult:
    BuyThenSell.reset()
    return await engine(candles, gate, progress=progress).run(spec(strategy, days, fills))


# A session laid out for `buy_at=2, sell_at=5` (see test_engine): the buy signal is answered on
# bar 3, the sell signal on bar 6.
WORKED_DAY = [
    ("100", "101", "99", "100"),
    ("100", "102", "100", "101"),  # bar 2: BUY signal at 101; this bar's low WOULD fill 101.10
    ("101", "103", "100.5", "102"),  # bar 3: the buy fills here, at 101.10
    ("102", "104", "101.5", "103"),
    ("103", "105", "102.5", "104"),  # bar 5: SELL signal at 104
    ("104", "105", "103", "104.5"),  # bar 6: the sell fills here, at 103.90
    ("104.5", "105", "104", "104.5"),
]


# A falling session: the mirror of WORKED_DAY. What wins on a rising day loses on this one.
DOWN_DAY = [
    ("100", "101", "99", "100"),
    ("100", "100.5", "98", "99"),  # bar 2: BUY signal at 99 -> limit 99.05
    ("99", "99.2", "97", "98"),  # bar 3: the buy fills at 99.05
    ("98", "98.5", "96", "97"),
    ("97", "97.5", "95", "96"),  # bar 5: SELL signal at 96 -> limit 95.95
    ("96", "96.5", "94", "95"),  # bar 6: the sell fills at 95.95
    ("95", "95.5", "93", "94"),
]
