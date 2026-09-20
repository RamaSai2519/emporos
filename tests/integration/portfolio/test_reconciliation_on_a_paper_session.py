"""EM-85 — reconciliation against a REAL paper session on Atlas, and against a deliberately
corrupted one.

The stack under test is the production one, wired the way a worker wires it: the paper broker keeps
its own books, the execution engine and fill processor write the platform's books from what the
broker reports, and the reconciler compares the two. An undisturbed session must reconcile clean;
each injected fault must be caught as the RIGHT kind of discrepancy, and only fill lag may be
adopted automatically. The tape is synthetic (the market is closed); it changes nothing about the
mechanics under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from emporos.broker.models import MarketDataMode, PlaceOrderRequest
from emporos.broker.paper.broker import PaperBroker
from emporos.broker.paper.costs import ScheduledCosts
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.fills import ParticipationLiquidity
from emporos.cli.paper_composition import PaperComposer
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.fees import IntradayCharges
from emporos.domain.marketable import MarketableLimit
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.execution.engine import ExecutionEngine
from emporos.execution.fills import FillProcessor, FillSynchroniser
from emporos.execution.gateway import BrokerOrderGateway
from emporos.execution.pricing import MarketableLimitPricer
from emporos.execution.state import OrderStateMachine
from emporos.persistence.collections import Collection
from emporos.persistence.ledger_reader import PlatformLedger
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.order_journal import MongoOrderJournal
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    PositionRepository,
    ReconciliationRunRepository,
)
from emporos.persistence.transactions import TransactionRunner
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.portfolio.ledger import PositionCalculator
from emporos.portfolio.reconciliation import (
    DiscrepancyKind,
    Reconciler,
    ReconciliationComparator,
    ReconciliationTracker,
    ReconciliationTrigger,
)
from emporos.portfolio.replay import PositionReplay
from emporos.portfolio.sources import BrokerReconciliationSource
from emporos.risk.approval import RiskApprovedSignal
from emporos.risk.engine import RiskEngine
from emporos.risk.snapshot import ReconciliationStatus
from tests.support.execution import FixedTicks, RecordingLimiter
from tests.support.fakes import RecordingAlertSink, make_tick
from tests.support.paper_market import NOW, FakeMarketData
from tests.support.paper_mongo import PaperMongoRig
from tests.support.paper_rig import ID, SBIN
from tests.support.risk import MemoryRejectionLog, ScriptedRule, StaticSnapshots, healthy
from tests.support.strategies import make_signal

pytestmark = pytest.mark.integration
CASH = Money.of("1000000")


class RecordingHalt:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    async def halt(self, reason: str) -> None:
        self.reasons.append(reason)


@dataclass
class Session:
    rig: PaperMongoRig
    clock: FixedClock
    market: FakeMarketData
    broker: PaperBroker
    engine: ExecutionEngine
    sync: FillSynchroniser
    reconciler: Reconciler
    halt: RecordingHalt
    tracker: ReconciliationTracker
    runs: ReconciliationRunRepository
    platform_orders: OrderRepository
    platform_events: OrderEventRepository
    platform_executions: ExecutionRepository
    platform_positions: PositionRepository
    risk: RiskEngine
    flush: Any
    volume: int = 5_000_000
    seq: int = 0

    async def approve(self, side: OrderSide, qty: int, price: str) -> RiskApprovedSignal:
        key = f"sig-{IdGenerator().new_ulid()}"
        signal = make_signal(instrument_id=ID, side=side, quantity=qty, price=price)
        decision = await self.risk.review(signal, key)
        assert isinstance(decision, RiskApprovedSignal)
        return decision

    async def trade(self, price: str, shares: int) -> None:
        """One tick at `price` in which `shares` traded; flush so the broker's books are durable."""
        self.clock.advance(timedelta(seconds=1))
        self.volume += shares
        self.seq += 1
        self.market.emit(
            make_tick(
                self.clock.now(), price, volume=self.volume, instrument_id=ID, sequence=self.seq
            )
        )
        await self.flush()

    async def round_trip(self) -> None:
        """Buy 100 @ 100.00, sell 60 @ 100.50: an open position and some realised P&L."""
        await self.trade("100.00", 0)  # the first tick only sets the volume baseline
        buy = await self.engine.place(await self.approve(OrderSide.BUY, 100, "100.00"))
        await self.trade("100.00", 300)
        await self.sync.sync()
        sell = await self.engine.place(await self.approve(OrderSide.SELL, 60, "100.50"))
        await self.trade("100.50", 300)
        await self.sync.sync()
        assert (await self.platform_orders.get(buy.id)).state == "FILLED"  # type: ignore[union-attr]
        assert (await self.platform_orders.get(sell.id)).state == "FILLED"  # type: ignore[union-attr]

    async def reconcile(self) -> Any:
        return await self.reconciler.run(ReconciliationTrigger.MANUAL)


@pytest.fixture
async def session(dev_settings: Any) -> AsyncIterator[Session]:
    mongo = MongoClientFactory(dev_settings)
    rig = PaperMongoRig(mongo.client, mongo.database())
    await rig.prepare()
    database, ids, clock = mongo.database(), IdGenerator(), FixedClock(NOW)
    market = FakeMarketData([SBIN], [])
    charges = ScheduledCosts(
        IntradayCharges(FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21)))
    )
    runtime = await PaperComposer(
        source=market,
        factory=PaperBrokerFactory(
            PaperBrokerConfig(rig.account_id, CASH),
            charges,
            liquidity=ParticipationLiquidity(Decimal(1)),
        ),
        journal=rig.journal(),
        store=rig.store,
        client_code=rig.account_id,
        clock=clock,
        ids=ids,
        sleeper=AsyncioSleeper(),
        alerts=LogAlertSink(),
    ).open()
    await runtime.broker.subscribe_market_data([ID], MarketDataMode.QUOTE)

    orders, events = OrderRepository(database), OrderEventRepository(database)
    executions, positions = ExecutionRepository(database), PositionRepository(database)
    journal = MongoOrderJournal(
        orders, events, executions, positions, TransactionRunner(mongo.client), rig.account_id
    )
    machine = OrderStateMachine()
    engine = ExecutionEngine(
        BrokerOrderGateway(runtime.broker), journal, RecordingLimiter(), clock, ids, machine,
        MarketableLimitPricer(MarketableLimit(Decimal(0)), FixedTicks()), rig.account_id,
        approval_lifetime=timedelta(hours=1),
    )  # fmt: skip
    sync = FillSynchroniser(
        runtime.broker,
        FillProcessor(journal, charges, PositionCalculator(), machine, clock, ids, rig.account_id),
    )
    tracker, halt = ReconciliationTracker(clock), RecordingHalt()
    runs = ReconciliationRunRepository(database)
    log = _RunLog(runs)
    reconciler = Reconciler(
        BrokerReconciliationSource(
            PlatformLedger(rig.account_id, orders, executions, positions, clock), runtime.broker
        ),
        ReconciliationComparator(PositionReplay(PositionCalculator())),
        sync, log, halt, tracker, clock, ids, RecordingAlertSink(),
    )  # fmt: skip
    risk = RiskEngine(
        [ScriptedRule("allow")], StaticSnapshots(healthy()), MemoryRejectionLog(), ids, clock,
        RecordingAlertSink(),
    )  # fmt: skip
    s = Session(
        rig, clock, market, runtime.broker, engine, sync, reconciler, halt, tracker, runs,
        orders, events, executions, positions, risk, rig.journal().flush,
    )  # fmt: skip
    try:
        yield s
    finally:
        mine = {"account_id": rig.account_id}
        ids_ = [o.id for o in await orders.find(mine)]
        for name in (Collection.ORDERS, Collection.EXECUTIONS, Collection.POSITIONS):
            await database[name].delete_many(mine)
        await database[Collection.ORDER_EVENTS].delete_many({"order_id": {"$in": ids_}})
        await database[Collection.RECONCILIATION_RUNS].delete_many({"_id": {"$in": log.ids}})
        await rig.cleanup()
        await mongo.close()


class _RunLog:
    """Persists each run through the real repository and remembers which rows are ours."""

    def __init__(self, runs: ReconciliationRunRepository) -> None:
        self._runs = runs
        self.ids: list[str] = []

    async def record(self, record: Any) -> None:
        self.ids.append(record.id)
        await self._runs.insert(record)


class TestAnUndisturbedSession:
    async def test_reconciles_clean_and_the_books_agree_with_the_brokers(
        self, session: Session
    ) -> None:
        await session.round_trip()
        assert await session.reconcile() == ()
        assert session.halt.reasons == []
        assert session.tracker.status() is ReconciliationStatus.CLEAN
        (position,) = await session.platform_positions.open_for_account(session.rig.account_id)
        assert (position.net_quantity, position.average_price) == (40, Money.of("100.00"))
        # 60 sold at 100.50 against a cost of 100.00: 30 gross, less both legs' charges.
        assert position.gross_realised_pnl == Money.of("30.00")
        assert position.realised_pnl < Money.of("30.00")
        record = (await session.runs.find({"trigger": "MANUAL"}))[-1]
        assert record.status == "CLEAN" and record.discrepancies == []

    async def test_a_restart_changes_nothing_reconciliation_still_clean(
        self, session: Session
    ) -> None:
        await session.round_trip()
        await session.engine.recover()  # what a restarted worker does first
        await session.sync.sync()
        assert await session.reconcile() == ()

    async def test_every_order_has_a_contiguous_event_history_ending_in_its_state(
        self, session: Session
    ) -> None:
        await session.round_trip()
        for order in await session.platform_orders.for_account_session(
            session.rig.account_id, "2026-09-18"
        ):
            events = await session.platform_events.for_order(order.id)
            assert [e.seq for e in events] == list(range(1, len(events) + 1))
            assert events[-1].state == order.state
            assert events[-1].filled_quantity == order.filled_quantity


class TestInjectedFaults:
    async def test_a_corrupted_position_is_flagged_and_trading_halts(
        self, session: Session
    ) -> None:
        await session.round_trip()
        (position,) = await session.platform_positions.open_for_account(session.rig.account_id)
        await session.platform_positions.replace(position.model_copy(update={"net_quantity": 41}))
        found = await session.reconcile()
        kinds = {d.kind for d in found}
        assert {
            DiscrepancyKind.POSITION_MISMATCH,
            DiscrepancyKind.POSITION_HISTORY_MISMATCH,
        } <= kinds
        assert len(session.halt.reasons) == 1
        assert session.tracker.status() is ReconciliationStatus.FAILED
        assert (await session.runs.find({"trigger": "MANUAL"}))[-1].status == "FAILED"

    async def test_a_manual_order_at_the_broker_is_flagged_and_never_adopted(
        self, session: Session
    ) -> None:
        await session.round_trip()
        await BrokerOrderGateway(session.broker).submit(
            PlaceOrderRequest(ID, OrderSide.BUY, OrderType.LIMIT, 5, Money.of("90.00"), "MANUAL1")
        )  # placed outside the execution engine, as from the broker's own app
        await session.flush()
        found = await session.reconcile()
        assert DiscrepancyKind.EXTERNAL_ORDER in {d.kind for d in found}
        assert len(session.halt.reasons) == 1
        assert await session.platform_orders.get_by_ordertag("MANUAL1") is None  # nothing adopted

    async def test_a_deleted_execution_is_not_silently_re_created(self, session: Session) -> None:
        await session.round_trip()
        (first, *_) = await session.platform_executions.for_account(session.rig.account_id)
        await session.platform_executions.delete(first.id)
        found = await session.reconcile()
        assert found and len(session.halt.reasons) == 1
        # Re-applying the broker's fill would overfill the order we already show as filled: refused.
        assert first.broker_trade_id in {d.reference for d in found} or any(
            d.kind is DiscrepancyKind.ORDER_FILL_MISMATCH for d in found
        )

    async def test_fills_we_have_not_applied_yet_are_adopted_not_halted(
        self, session: Session
    ) -> None:
        await session.trade("100.00", 0)
        await session.engine.place(await session.approve(OrderSide.BUY, 100, "100.00"))
        await session.trade("100.00", 300)  # the broker fills it; we never sync
        found = await session.reconcile()
        assert found == () and session.halt.reasons == []
        record = (await session.runs.find({"trigger": "MANUAL"}))[-1]
        assert record.status == "HEALED" and record.healed
        assert session.tracker.status() is ReconciliationStatus.CLEAN
