"""Real Mongo verifies atomic state/audit commits, atomic idempotent fills, stale-writer rejection.

Every id is randomly suffixed and only this test's own account is ever cleaned up (plan.md §6.0:
the dev database is shared)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from emporos.broker.models import BrokerTrade
from emporos.core.clock import SystemClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.execution.fills import FillProcessor, FillResult
from emporos.execution.state import OrderStateMachine
from emporos.persistence.collections import Collection
from emporos.persistence.errors import ConcurrentModificationError, DuplicateRecordError
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.order_journal import MongoOrderJournal
from emporos.persistence.records import ExecutionRecord, PositionRecord
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    PositionRepository,
)
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.persistence.transactions import TransactionRunner
from emporos.portfolio.ledger import PositionCalculator
from tests.support.execution import ZeroCosts
from tests.support.records import RecordFactory

pytestmark = pytest.mark.integration


class Rig:
    def __init__(self, dev_settings: Any) -> None:
        self.mongo = MongoClientFactory(dev_settings)
        self.database = self.mongo.database()
        self.orders = OrderRepository(self.database)
        self.events = OrderEventRepository(self.database)
        self.executions = ExecutionRepository(self.database)
        self.positions = PositionRepository(self.database)
        self.account = "EXECIT" + IdGenerator().new_ulid()

    def journal(self, account: str | None = None) -> MongoOrderJournal:
        return MongoOrderJournal(
            self.orders, self.events, self.executions, self.positions,
            TransactionRunner(self.mongo.client), account or self.account,
        )  # fmt: skip

    async def cleanup(self) -> None:
        mine = {"account_id": self.account}
        ids = [o.id for o in await self.orders.find(mine)]
        for collection in (Collection.ORDERS, Collection.EXECUTIONS, Collection.POSITIONS):
            await self.database[collection].delete_many(mine)
        await self.database[Collection.ORDER_EVENTS].delete_many({"order_id": {"$in": ids}})
        await self.mongo.close()


@pytest.fixture
async def rig(dev_settings: Any) -> AsyncIterator[Rig]:
    rig = Rig(dev_settings)
    await MigrationRunner(MongoSchemaStore(rig.database), PLATFORM_SCHEMA).apply()
    try:
        yield rig
    finally:
        await rig.cleanup()


class TestOrderState:
    async def test_state_and_events_commit_together_and_replay_cannot_duplicate(
        self, rig: Rig
    ) -> None:
        journal = rig.journal()
        order = RecordFactory().order(account_id=rig.account)
        order = order.model_copy(
            update={"updated_at": order.updated_at.replace(microsecond=123456)}
        )
        await journal.create(order)
        stored = await journal.get(order.id)
        assert stored is not None and stored.id == order.id
        assert stored.updated_at.microsecond == 123000
        assert await journal.by_key(order.idempotency_key) == stored
        assert await journal.unresolved(order.instrument_id)
        assert await journal.active() == [stored]
        with pytest.raises(DuplicateRecordError):
            await journal.create(order)
        opened = order.model_copy(update={"state": "OPEN", "broker_order_id": "test"})
        await journal.transition(order, opened)
        with pytest.raises(ConcurrentModificationError, match="concurrently"):
            await journal.transition(order, opened)
        assert not await journal.unresolved(order.instrument_id)
        assert await journal.by_broker_order_id("test") is not None
        assert [(e.seq, e.state) for e in await journal.history(order.id)] == [
            (1, "PENDING_NEW"),
            (2, "OPEN"),
        ]

    async def test_a_journal_sees_and_changes_only_its_own_accounts_orders(self, rig: Rig) -> None:
        order = RecordFactory().order(account_id=rig.account)
        await rig.journal().create(order)
        opened = order.model_copy(update={"state": "OPEN", "broker_order_id": "x"})
        other = rig.journal("other")
        assert await other.get(order.id) is None
        assert await other.by_key(order.idempotency_key) is None
        assert await other.active() == []
        with pytest.raises(ValueError, match="account"):
            await other.create(order)
        with pytest.raises(ValueError, match="ownership"):
            await other.transition(order, opened)


class TestFills:
    async def _open(self, rig: Rig) -> Any:
        journal = rig.journal()
        order = RecordFactory().order(
            account_id=rig.account, quantity=10, instrument_id="NSE:9999", side=OrderSide.BUY
        )
        await journal.create(order)
        opened = order.model_copy(update={"state": "OPEN", "broker_order_id": f"B-{order.id}"})
        await journal.transition(order, opened)
        processor = FillProcessor(
            journal, ZeroCosts(), PositionCalculator(), OrderStateMachine(), SystemClock(),
            IdGenerator(), rig.account,
        )  # fmt: skip
        return journal, opened, processor

    @staticmethod
    def _trade(order: Any, trade_id: str, quantity: int, price: str) -> BrokerTrade:
        return BrokerTrade(
            trade_id, order.broker_order_id, order.instrument_id, order.side, quantity,
            Money.of(price), SystemClock().now(),
        )  # fmt: skip

    async def test_a_redelivered_fill_leaves_exactly_one_execution_and_one_position_update(
        self, rig: Rig
    ) -> None:
        journal, order, processor = await self._open(rig)
        trade = self._trade(order, f"T-{order.id}", 4, "100")
        assert await processor.process(trade) == FillResult.APPLIED
        assert await processor.process(trade) == FillResult.DUPLICATE
        assert await processor.process(trade) == FillResult.DUPLICATE
        executions = await rig.executions.for_order(order.id)
        assert len(executions) == 1
        position = await journal.position(order.instrument_id)
        assert position is not None and position.net_quantity == 4
        stored = await journal.get(order.id)
        assert stored is not None
        assert (stored.state, stored.filled_quantity) == ("PARTIALLY_FILLED", 4)
        events = await journal.history(order.id)
        assert [(e.seq, e.state, e.filled_quantity) for e in events] == [
            (1, "PENDING_NEW", 0),
            (2, "OPEN", 0),
            (3, "PARTIALLY_FILLED", 4),
        ]

    async def test_a_fill_that_loses_a_race_with_the_orders_state_writes_nothing(
        self, rig: Rig
    ) -> None:
        journal, order, _ = await self._open(rig)
        await journal.transition(order, order.model_copy(update={"state": "PENDING_CANCEL"}))
        stale = order.model_copy(update={"state": "PARTIALLY_FILLED", "filled_quantity": 4})
        with pytest.raises(ConcurrentModificationError):
            await journal.record_fill(
                order,
                stale,
                _execution(order, rig.account, "T-race"),
                _position(order, rig.account),
            )
        assert await rig.executions.get_by_broker_trade_id("T-race") is None  # rolled back
        assert await journal.position(order.instrument_id) is None

    async def test_a_fill_cannot_cross_accounts(self, rig: Rig) -> None:
        journal, order, _ = await self._open(rig)
        with pytest.raises(ValueError, match="accounts"):
            await journal.record_fill(
                order,
                order,
                _execution(order, "someone-else", "T-x"),
                _position(order, rig.account),
            )


def _execution(order: Any, account: str, trade_id: str) -> ExecutionRecord:
    return ExecutionRecord(
        _id=f"exec-{trade_id}", broker_trade_id=trade_id, order_id=order.id,
        instrument_id=order.instrument_id, side=order.side, quantity=4, price=Money.of("100"),
        ts=SystemClock().now(), account_id=account, session_date=order.session_date,
    )  # fmt: skip


def _position(order: Any, account: str) -> PositionRecord:
    return PositionRecord(
        _id=f"{account}:{order.instrument_id}", account_id=account,
        instrument_id=order.instrument_id, net_quantity=4, average_price=Money.of("100"),
        realised_pnl=Money.zero(), updated_at=SystemClock().now(),
    )  # fmt: skip
