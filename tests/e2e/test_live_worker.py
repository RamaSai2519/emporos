"""EM-142/EM-143 — the live composition proved on the emulator, never a real endpoint.

`LiveWorkerComposer` is the ONLY composer allowed to hand a strategy an order-capable broker.
This runs it for real, over `AngelOneBroker` and `FakeSmartApi` (the same emulator pair the chaos
suite already drills for lost replies, restarts and redelivered fills at the execution-engine
layer), behind a `LiveVenue` whose order-update socket has confirmed connected. A strategy signal
must travel through risk, execution and the state machine to a real order at the emulator broker —
and `TradingModeGuard` must block it when `LIVE_TRADING_ENABLED` is not true, proving the safety
invariant the live composition depends on holds over the real composition root, not just in a rule
unit test.

Unlike the paper broker, the emulator does not fill an order from ticks on its own — the exchange
side is played explicitly (`BrokerHarness.fill`, the same hook the chaos suite drives). The tape
here polls the broker's own order book and fills whatever it finds open, exactly as
`test_execution_chaos.py`'s seeded session does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from emporos.broker.models import BrokerOrder
from emporos.cli.live_venue import LiveVenue
from emporos.cli.worker_composition import LiveWorkerComposer, WorkerTuning, bar_queue_for
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.marketdata.session import SessionWindow
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.kill_switch import FileSentinelKillSwitch
from emporos.session.lifecycle import SessionState
from emporos.session.risk_facts import VenueHealth
from emporos.session.worker import SessionSchedule
from emporos.strategies.registry import StrategyRegistry
from tests.contract.harnesses import BrokerHarness, angelone
from tests.support.fakes import make_tick
from tests.support.paper_rig import ID
from tests.support.worker_rig import EnterOnce, RiskLimitsForTests, at, bar, strategy_config

pytestmark = pytest.mark.integration

FILL_PRICE = "100.00"


class _FakeSocket:
    """`live_venue.SocketClient`: a no-op background task. `LiveVenue.connect()` only schedules
    the real order socket's `run()` as a task — it does not await it — so its arrival is not
    ordered against anything the worker does next; `test_live_venue.py` proves that connection
    path itself (needing an explicit loop turn to observe it). Here the order feed is marked up
    directly, up front, so this suite is free to test what it is actually for: order placement
    mechanics over the emulator once every precondition already holds."""

    def __init__(self) -> None:
        self.stopped = False

    async def run(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True


@dataclass
class LiveTape:
    """The same scripted-clock pattern `tests/support/worker_rig.py` uses for paper: ticks and
    closed bars release as the clock advances. It additionally plays the exchange side for
    whatever the strategy places, the way `test_execution_chaos.py`'s seeded session does — the
    emulator, unlike the paper broker, never fills an order on its own."""

    clock: FixedClock
    harness: BrokerHarness
    on_bar: Callable[[Candle], None]
    ticks: list[tuple[Any, str, int]] = field(default_factory=list)
    bars: list[Candle] = field(default_factory=list)
    volume: int = 5_000_000
    seq: int = 0

    def _release(self) -> None:
        now = self.clock.now()
        for when, price, shares in sorted(t for t in self.ticks if t[0] <= now):
            self.volume += shares
            self.seq += 1
            self.harness.emit_tick(
                make_tick(when, price, volume=self.volume, instrument_id=ID, sequence=self.seq)
            )
        self.ticks = [t for t in self.ticks if t[0] > now]
        for closed in [b for b in self.bars if b.closes_at <= now]:
            self.on_bar(closed)
        self.bars = [b for b in self.bars if b.closes_at > now]

    async def sleep(self, seconds: float) -> None:
        self.clock.advance(timedelta(seconds=seconds))
        self._release()
        for held in await self.harness.broker.get_order_book():
            if held.status.is_terminal:
                continue  # already CANCELLED/FILLED/REJECTED: FakeSmartApi never zeroes out
                # quantity on cancel, so a blind remaining>0 check here would "fill" an order
                # the repricer (or anything else) had already legitimately cancelled, corrupting
                # the emulator's own state (EM-145's real root cause — a test-harness bug).
            remaining = held.quantity - held.filled_quantity
            if remaining and self._marketable(held):
                self.harness.fill(held.broker_order_id or "", remaining, Money.of(FILL_PRICE))

    @staticmethod
    def _marketable(held: BrokerOrder) -> bool:
        """A resting limit order only fills when the tape's price would actually cross it — a
        BUY at or above FILL_PRICE, a SELL at or below it — exactly like a real exchange's
        price-time priority. Blindly filling every held order regardless of its limit price (the
        original behaviour) force-filled a deliberately-unmarketable manual test order (a resting
        BUY well below the tape price, meant to stay open until the test cancels it) within one
        poll cycle, leaving it FILLED by the time the test tried to cancel it."""
        if held.price is None:
            return True  # not expected for a limit-only platform, but never silently skip a fill
        fill = Money.of(FILL_PRICE).amount
        if held.side is OrderSide.BUY:
            return held.price.amount >= fill
        return held.price.amount <= fill


def _normal_tape(
    clock: FixedClock, harness: BrokerHarness, on_bar: Callable[[Candle], None]
) -> LiveTape:
    tape = LiveTape(clock, harness, on_bar)
    minute = timedelta(minutes=1)
    when = at(9, 30) + minute
    while when <= at(10, 0):
        tape.ticks.append((when, FILL_PRICE, 400))
        when += minute
    tape.bars.append(bar(9, 30))  # closes 09:35, the strategy's entry
    return tape


@pytest.fixture
async def mongo(dev_settings: Any) -> AsyncIterator[MongoClientFactory]:
    factory = MongoClientFactory(dev_settings)
    await MigrationRunner(MongoSchemaStore(factory.database()), PLATFORM_SCHEMA).apply()
    yield factory
    await factory.close()


async def _cleanup(mongo: MongoClientFactory, account_id: str, kill_switch_collection: str) -> None:
    db = mongo.database()
    mine = {"account_id": account_id}
    for name in (
        Collection.ORDERS, Collection.EXECUTIONS, Collection.POSITIONS,
        Collection.PORTFOLIO_SNAPSHOTS,
    ):  # fmt: skip
        await db[name].delete_many(mine)
    await db[Collection.SYSTEM_EVENTS].delete_many({"account_id": account_id})
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
    await db.drop_collection(kill_switch_collection)


def _tuning() -> WorkerTuning:
    return WorkerTuning(
        kill_switch=timedelta(seconds=30),
        schedule=SessionSchedule(
            square_off_at=time(9, 50), close_at=time(10, 0), poll_interval=timedelta(seconds=30)
        ),
    )


async def _run(
    mongo: MongoClientFactory, sentinel_path: Path, *, live_trading_enabled: bool
) -> tuple[Any, BrokerHarness]:
    """Build a `LiveWorkerComposer` over the Angel One emulator and run one ordinary session.
    Returns (SessionReport, BrokerHarness) so the test can inspect what reached the emulator."""
    suffix = IdGenerator().new_ulid().lower()
    account_id = f"LIVEIT{suffix.upper()}"
    kill_switch_collection = f"zz_kill_switch_{suffix}"
    harness = angelone()
    health = VenueHealth()
    health.set_feed(True)  # the order feed's own connection timing is test_live_venue.py's job
    venue = LiveVenue(harness.broker, _FakeSocket(), _FakeSocket(), [ID], health)

    registry = StrategyRegistry()
    registry.register(EnterOnce)
    # reprice_after_seconds=60 (the shared default) leaves almost no margin against poll_interval
    # (30s): any real-world latency between the entry filling at the emulator and our own sync
    # picking it up can push the repricer into cancelling an order the exchange already completed
    # underneath it (FakeSmartApi now refuses that specific case, but the race is still not this
    # proof's concern) — a long reprice window keeps this session about proving the composition,
    # not about racing the repricer.
    base = strategy_config()
    config = base.model_copy(
        update={"execution": base.execution.model_copy(update={"reprice_after_seconds": 3600})}
    )
    bars = bar_queue_for([config])
    clock = FixedClock(at(9, 0))
    tape = _normal_tape(clock, harness, bars.on_candle)

    composer = LiveWorkerComposer(
        client=mongo.client,
        database=mongo.database(),
        broker=harness.broker,
        venue=venue,
        health=health,
        live_trading_enabled=live_trading_enabled,
        bars=bars,
        registry=registry,
        configs=[config],
        limits=RiskLimitsForTests.limits(),
        fees=FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21)),
        account_id=account_id,
        clock=clock,
        sleeper=tape,
        ids=IdGenerator(),
        kill_switch_sentinel=FileSentinelKillSwitch(sentinel_path),
        window=SessionWindow(),
        tuning=_tuning(),
        session_date=date(2026, 9, 18),
        kill_switch_collection=kill_switch_collection,
    )
    try:
        assembly = await composer.build()
        report = await assembly.worker.run_session()
    finally:
        await _cleanup(mongo, account_id, kill_switch_collection)
    return report, harness


class TestTheLiveCompositionOnTheEmulator:
    async def test_an_ordinary_session_enters_squares_off_and_ends_flat_reconciled(
        self, mongo: MongoClientFactory, tmp_path: Path
    ) -> None:
        report, harness = await _run(mongo, tmp_path / "HALT", live_trading_enabled=True)

        assert report.final_state is SessionState.SHUTTING_DOWN and report.failure == ""
        assert report.flat_at_close is True
        assert report.close_out is not None and report.close_out.discrepancies == 0
        assert report.square_off is not None and report.square_off.submitted == (ID,)

        book = await harness.broker.get_order_book()
        assert sorted((o.side.value, o.filled_quantity) for o in book) == [
            ("BUY", 100), ("SELL", 100),
        ]  # fmt: skip

    async def test_trading_mode_guard_blocks_the_order_when_live_trading_is_not_enabled(
        self, mongo: MongoClientFactory, tmp_path: Path
    ) -> None:
        report, harness = await _run(mongo, tmp_path / "HALT", live_trading_enabled=False)

        assert report.final_state is SessionState.SHUTTING_DOWN
        book = await harness.broker.get_order_book()
        assert book == []  # the signal was generated but never reached the broker
