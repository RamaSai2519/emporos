"""A whole paper trading session, driven in virtual time against real Atlas.

The worker is composed by the PRODUCTION composer (`PaperWorkerComposer`); only the outside world
is scripted: a market tape, a strategy, and a clock that advances when the worker sleeps. Nothing
here can reach a broker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pymongo import AsyncMongoClient

from emporos.cli.worker_composition import (
    PaperWorkerComposer,
    WorkerAssembly,
    WorkerTuning,
    bar_queue_for,
)
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.domain.ticks import Tick
from emporos.marketdata.session import SessionWindow
from emporos.persistence.collections import Collection
from emporos.persistence.mongo import MongoClientFactory
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.portfolio.snapshots import SnapshotSchedule
from emporos.risk.kill_switch import FileSentinelKillSwitch
from emporos.risk.limits import RiskLimits
from emporos.session.worker import SessionSchedule
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
from emporos.strategies.registry import StrategyRegistry
from tests.support.fakes import make_tick
from tests.support.paper_market import NOW, FakeMarketData
from tests.support.paper_rig import ID, SBIN

DAY = "2026-09-18"


class EnterOnceParameters(StrategyParameters):
    pass


class EnterOnce(Strategy):
    """Buys 100 on the first closed bar, once, and never sells: the session's square-off must."""

    name = "enter_once_test"
    parameters_model = EnterOnceParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        super().__init__(config)
        self._ctx: StrategyContext | None = None
        self._entered = False
        self._outbox: list[Signal] = []
        self.session_ended = False

    def initialize(self, ctx: StrategyContext) -> None:
        self._ctx = ctx

    def on_market_data(self, event: Candle | Tick) -> None:
        assert self._ctx is not None
        if isinstance(event, Candle) and not self._entered:
            self._entered = True
            self._outbox.append(
                Signal(
                    self._ctx.run_id, event.instrument_id, SignalKind.ENTRY, OrderSide.BUY,
                    OrderType.LIMIT, 100, event.close, event.closes_at, "first bar",
                )
            )  # fmt: skip

    def generate_signal(self) -> Signal | None:
        return self._outbox.pop(0) if self._outbox else None

    def on_session_end(self) -> None:
        self.session_ended = True


def strategy_config(name: str = EnterOnce.name) -> ResolvedStrategyConfig:
    return ResolvedStrategyConfig(
        name=name,
        enabled=True,
        timeframe=Timeframe.M5,
        universe=(UniverseMember(symbol="NSE:SBIN-EQ", instrument_id=ID),),
        parameters=EnterOnceParameters(),
        risk=RiskSettings(
            max_position_value=Decimal(50000), max_open_positions=3,
            stop_loss_pct=Decimal(1), target_pct=Decimal(2),
        ),
        execution=ExecutionSettings(
            limit_buffer_bps=Decimal(5), reprice_after_seconds=60, max_reprices=3
        ),
        session=SessionSettings.model_validate(
            {"no_new_entries_after": "09:45", "square_off_at": "09:50"}
        ),
    )  # fmt: skip


class Crash(BaseException):
    """The process dies at a chosen moment. Not an `Exception`: nothing may catch it."""


@dataclass
class Tape:
    """What the market does, and when. Advancing the clock releases everything now due."""

    clock: FixedClock
    market: FakeMarketData
    on_bar: Callable[[Candle], None]
    ticks: list[tuple[datetime, str, int]] = field(default_factory=list)  # (when, price, shares)
    bars: list[Candle] = field(default_factory=list)
    crash_at: datetime | None = None
    hooks: list[tuple[datetime, Callable[[], Awaitable[object]]]] = field(default_factory=list)
    volume: int = 5_000_000
    seq: int = 0

    def _release(self) -> None:
        now = self.clock.now()
        for when, price, shares in sorted(t for t in self.ticks if t[0] <= now):
            self.volume += shares
            self.seq += 1
            self.market.emit(
                make_tick(when, price, volume=self.volume, instrument_id=ID, sequence=self.seq)
            )
        self.ticks = [t for t in self.ticks if t[0] > now]
        for bar in [b for b in self.bars if b.closes_at <= now]:
            self.on_bar(bar)
        self.bars = [b for b in self.bars if b.closes_at > now]

    async def sleep(self, seconds: float) -> None:
        self.clock.advance(timedelta(seconds=seconds))
        self._release()
        for hook in [h for h in self.hooks if h[0] <= self.clock.now()]:
            self.hooks.remove(hook)
            await hook[1]()
        if self.crash_at is not None and self.clock.now() >= self.crash_at:
            raise Crash


def at(hh: int, mm: int, ss: int = 0) -> datetime:
    """`hh:mm` IST on the session day, as UTC."""
    return datetime(2026, 9, 18, hh, mm, ss, tzinfo=UTC) - timedelta(hours=5, minutes=30)


def bar(hh: int, mm: int, close: str = "100.00") -> Candle:
    start = at(hh, mm)
    price = Money.of(close)
    return Candle(ID, Timeframe.M5, start, price, price, price, price, 1000)


def normal_tape(
    clock: FixedClock, market: FakeMarketData, on_bar: Callable[[Candle], None]
) -> Tape:
    """Trades at 100.00 all session, with plenty of volume: a buy fills, and so does the exit."""
    tape = Tape(clock, market, on_bar)
    minute = timedelta(minutes=1)
    when = at(9, 30) + minute
    while when <= at(10, 0):
        tape.ticks.append((when, "100.00", 400))
        when += minute
    tape.bars.append(bar(9, 30))  # closes 09:35, the strategy's entry
    return tape


@dataclass
class WorkerWorld:
    mongo: MongoClientFactory
    client: AsyncMongoClient[Any]
    account_id: str
    sentinel_path: Path
    kill_switch_collection: str
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    ids: IdGenerator = field(default_factory=IdGenerator)
    market: FakeMarketData = field(default_factory=lambda: FakeMarketData([SBIN], []))
    strategy_name: str = ""
    system_event_ids: list[str] = field(default_factory=list)

    async def build(self, tape: Tape | None = None, **extra: Any) -> tuple[WorkerAssembly, Tape]:
        """A worker over this world. Passing the previous run's tape models a RESTART: the market
        carries on where it was and the new process sees only what comes after."""
        registry = StrategyRegistry()
        registry.register(EnterOnce)
        config = strategy_config()
        bars = bar_queue_for([config])
        tape = tape or normal_tape(self.clock, self.market, bars.on_candle)
        tape.on_bar = bars.on_candle
        tape.crash_at = None
        tuning = WorkerTuning(
            kill_switch=timedelta(seconds=30),
            schedule=SessionSchedule(
                square_off_at=time(9, 50), close_at=time(10, 0), poll_interval=timedelta(seconds=30)
            ),
            snapshot=SnapshotSchedule(interval=timedelta(minutes=5)),
        )
        composer = PaperWorkerComposer(
            client=self.client,
            database=self.mongo.database(),
            market=self.market,
            bars=bars,
            registry=registry,
            configs=[config],
            limits=RiskLimitsForTests.limits(),
            fees=FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21)),
            account_id=self.account_id,
            clock=self.clock,
            sleeper=tape,
            ids=self.ids,
            kill_switch_sentinel=FileSentinelKillSwitch(self.sentinel_path),
            window=SessionWindow(),
            tuning=tuning,
            session_date=date(2026, 9, 18),
            kill_switch_collection=self.kill_switch_collection,
            **extra,
        )
        return await composer.build(), tape


class RiskLimitsForTests:
    @staticmethod
    def limits() -> RiskLimits:
        from emporos.risk.config import RiskLimitsLoader

        return RiskLimitsLoader().load()


async def cleanup(world: WorkerWorld) -> None:
    """Delete only what this world wrote: its account's rows, its strategy, its own events."""
    db = world.mongo.database()
    mine = {"account_id": world.account_id}
    order_ids = [d["_id"] async for d in db[Collection.ORDERS].find(mine, {"_id": 1})]
    paper_ids = [d["_id"] async for d in db[Collection.PAPER_ORDERS].find(mine, {"_id": 1})]
    for name in (
        Collection.ORDERS, Collection.EXECUTIONS, Collection.POSITIONS,
        Collection.PORTFOLIO_SNAPSHOTS, Collection.PAPER_ORDERS, Collection.PAPER_EXECUTIONS,
        Collection.PAPER_POSITIONS, Collection.PAPER_PORTFOLIO_SNAPSHOTS,
    ):  # fmt: skip
        await db[name].delete_many(mine)
    await db[Collection.ORDER_EVENTS].delete_many({"order_id": {"$in": order_ids}})
    await db[Collection.PAPER_ORDER_EVENTS].delete_many({"order_id": {"$in": paper_ids}})
    strategy = await db[Collection.STRATEGIES].find_one({"name": EnterOnce.name})
    if strategy is not None:
        runs = [
            d["_id"]
            async for d in db[Collection.STRATEGY_RUNS].find({"strategy_id": strategy["_id"]})
        ]
        await db[Collection.SIGNALS].delete_many({"strategy_run_id": {"$in": runs}})
        await db[Collection.RISK_EVENTS].delete_many({"strategy_run_id": {"$in": runs}})
        await db[Collection.STRATEGY_RUNS].delete_many({"strategy_id": strategy["_id"]})
        await db[Collection.STRATEGIES].delete_one({"_id": strategy["_id"]})
    day = {"$gte": at(0, 0), "$lt": at(0, 0) + timedelta(days=1)}
    await db[Collection.SYSTEM_EVENTS].delete_many(
        {"type": "session_state", "account_id": world.account_id, "ts": day}
    )
    await db[Collection.SYSTEM_EVENTS].delete_many({"type": "alert", "ts": day})
    await db[Collection.RECONCILIATION_RUNS].delete_many({"ts": day})
    await db.drop_collection(world.kill_switch_collection)
