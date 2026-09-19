"""The one multi-document transaction (plan.md §6): applying a fill is atomic and idempotent."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
)
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.persistence.transactions import (
    FillApplication,
    FillApplier,
    FillOutcome,
    TransactionRunner,
)
from tests.support.records import RecordFactory

pytestmark = pytest.mark.integration


@dataclass
class Rig:
    applier: FillApplier
    orders: OrderRepository
    executions: ExecutionRepository
    positions: PositionRepository
    factory: RecordFactory


@pytest.fixture
async def rig(dev_settings: Any) -> AsyncIterator[Rig]:
    mongo = MongoClientFactory(dev_settings)
    database: AsyncDatabase[Mapping[str, Any]] = mongo.database()
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    orders, executions, positions = (
        OrderRepository(database),
        ExecutionRepository(database),
        PositionRepository(database),
    )
    try:
        yield Rig(
            FillApplier(TransactionRunner(mongo.client), orders, executions, positions),
            orders,
            executions,
            positions,
            RecordFactory(),
        )
    finally:
        await mongo.close()


async def test_a_fill_updates_order_execution_and_position_together(rig: Rig) -> None:
    order = rig.factory.order(state="OPEN")
    await rig.orders.insert(order)
    fill = FillApplication(
        execution=rig.factory.execution(order_id=order.id),
        order=order.model_copy(update={"state": "FILLED", "filled_quantity": 10}),
        position=rig.factory.position(),
    )
    try:
        assert await rig.applier.apply(fill) is FillOutcome.APPLIED

        stored_order = await rig.orders.get(order.id)
        assert stored_order is not None
        assert stored_order.state == "FILLED"
        assert await rig.executions.get(fill.execution.id) == fill.execution
        assert await rig.positions.get(fill.position.id) == fill.position
    finally:
        await rig.orders.delete(order.id)
        await rig.executions.delete(fill.execution.id)
        await rig.positions.delete(fill.position.id)


async def test_a_redelivered_fill_is_a_no_op(rig: Rig) -> None:
    order = rig.factory.order(state="OPEN")
    await rig.orders.insert(order)
    execution = rig.factory.execution(order_id=order.id)
    position = rig.factory.position()
    first = FillApplication(execution, order.model_copy(update={"state": "FILLED"}), position)
    redelivery = FillApplication(
        rig.factory.execution(order_id=order.id, broker_trade_id=execution.broker_trade_id),
        order.model_copy(update={"state": "CANCELLED"}),
        position.model_copy(update={"net_quantity": 999}),
    )
    try:
        assert await rig.applier.apply(first) is FillOutcome.APPLIED
        assert await rig.applier.apply(redelivery) is FillOutcome.DUPLICATE

        stored_order = await rig.orders.get(order.id)
        stored_position = await rig.positions.get(position.id)
        assert stored_order is not None and stored_order.state == "FILLED"
        assert stored_position is not None and stored_position.net_quantity == 10
        assert await rig.executions.count({"order_id": order.id}) == 1
    finally:
        await rig.orders.delete(order.id)
        await rig.executions.delete(execution.id)
        await rig.positions.delete(position.id)


async def test_a_failure_part_way_through_rolls_everything_back(rig: Rig) -> None:
    order = rig.factory.order(state="OPEN")
    held = rig.factory.position()
    await rig.orders.insert(order)
    await rig.positions.insert(held)
    # Same (account, instrument) as `held` but a different _id: the position upsert — the last
    # of the three writes — violates the unique index after the first two already succeeded.
    conflicting = rig.factory.position(account_id=held.account_id, instrument_id=held.instrument_id)
    fill = FillApplication(
        execution=rig.factory.execution(order_id=order.id),
        order=order.model_copy(update={"state": "FILLED"}),
        position=conflicting,
    )
    try:
        with pytest.raises(DuplicateRecordError) as raised:
            await rig.applier.apply(fill)

        assert raised.value.key_fields == ("account_id", "instrument_id")
        assert await rig.executions.get(fill.execution.id) is None
        stored_order = await rig.orders.get(order.id)
        assert stored_order is not None and stored_order.state == "OPEN"
    finally:
        await rig.orders.delete(order.id)
        await rig.positions.delete(held.id)
