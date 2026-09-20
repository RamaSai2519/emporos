"""EM-64: the paper journal on the real Atlas `emporos_dev` — same collections, same unique
indexes, same fill transaction real trading uses. Journal entries are fed in directly (the
broker-driven scenarios live in tests/failure/)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson.decimal128 import Decimal128

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerTrade
from emporos.broker.paper.account import PositionState
from emporos.broker.paper.journal import (
    FillRecorded,
    OrderStateRecorded,
    SnapshotRecorded,
)
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.persistence.collections import Collection
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.records import OrderRecord, PositionRecord
from emporos.persistence.transactions import FillApplication, FillApplier, FillOutcome
from tests.support.paper_mongo import PaperMongoRig

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
DAY = "2026-09-18"
INSTRUMENT = "NSE:3045"
S = BrokerOrderStatus


@pytest.fixture
async def rig(dev_settings: Any) -> AsyncIterator[PaperMongoRig]:
    mongo = MongoClientFactory(dev_settings)
    r = PaperMongoRig(mongo.client, mongo.database())
    await r.prepare()
    try:
        yield r
    finally:
        await r.cleanup()
        await mongo.close()


class Session:
    """The journal entries of one order: placed, part-filled 4, then filled 6 more."""

    def __init__(self, account_id: str) -> None:
        ulid = IdGenerator().new_ulid()
        self.account_id = account_id
        self.order_id = f"PAPER{ulid}"
        self.tag = f"T{ulid[:12]}"

    def order(self, status: BrokerOrderStatus, filled: int, seq_time: int) -> BrokerOrder:
        return BrokerOrder(
            self.order_id, self.tag, INSTRUMENT, OrderSide.BUY, OrderType.LIMIT, 10, filled, status,
            Money.of("100.00"), average_price=Money.of("100.00") if filled else None,
            updated_at=T0 + timedelta(seconds=seq_time),
        )  # fmt: skip

    def placed(self) -> OrderStateRecorded:
        return OrderStateRecorded(self.account_id, DAY, self.order(S.OPEN, 0, 0), 1, T0)

    def fill(self, number: int, qty: int, status: BrokerOrderStatus, filled: int) -> FillRecorded:
        at = T0 + timedelta(seconds=number)
        trade = BrokerTrade(
            f"{self.order_id}-{number}", self.order_id, INSTRUMENT, OrderSide.BUY, qty,
            Money.of("100.00"), at,
        )  # fmt: skip
        position = PositionState(
            INSTRUMENT, filled, Money.of("100.00"), Money.zero(), Money.of(f"{number}.50")
        )
        return FillRecorded(
            self.account_id, DAY, self.order(status, filled, number), number + 1, at, trade,
            Money.of("1.50") if number == 1 else Money.of("2.00"), position, number,
        )  # fmt: skip

    def snapshot(self, trades: int) -> SnapshotRecorded:
        return SnapshotRecorded(
            self.account_id, DAY, T0 + timedelta(seconds=trades), Money.of("999995.00"),
            Money.of("-3.50"), Money.of("12.00"), Money.of("3.50"), trades,
            (PositionState(INSTRUMENT, 10, Money.of("100.00"), Money.zero(), Money.of("3.50")),),
        )  # fmt: skip

    def entries(self) -> list[Any]:
        return [
            self.placed(),
            self.fill(1, 4, S.PARTIALLY_FILLED, 4),
            self.snapshot(1),
            self.fill(2, 6, S.FILLED, 10),
            self.snapshot(2),
        ]


async def write(rig: PaperMongoRig, entries: list[Any]) -> Any:
    journal = rig.journal()
    for entry in entries:
        journal.append(entry)
    await journal.flush()
    return journal


async def test_a_session_lands_in_the_real_collections_and_reads_back_exactly(
    rig: PaperMongoRig,
) -> None:
    s = Session(rig.account_id)

    journal = await write(rig, s.entries())

    assert journal.pending == 0
    order = await rig.orders.get_by_ordertag(s.tag)
    assert order is not None
    assert (order.state, order.filled_quantity, order.account_id) == ("FILLED", 10, rig.account_id)
    assert (order.limit_price, order.average_price) == (Money.of("100.00"), Money.of("100.00"))
    assert [(e.seq, e.state, e.filled_quantity) for e in await rig.events.for_order(order.id)] == [
        (1, "OPEN", 0), (2, "PARTIALLY_FILLED", 4), (3, "FILLED", 10),
    ]  # fmt: skip
    executions = await rig.executions.for_order(order.id)
    assert [(x.broker_trade_id, x.quantity, x.fees) for x in executions] == [
        (f"{s.order_id}-1", 4, Money.of("1.50")), (f"{s.order_id}-2", 6, Money.of("2.00")),
    ]  # fmt: skip
    position = await rig.positions.get_for(rig.account_id, INSTRUMENT)
    assert position is not None and (position.net_quantity, position.realised_pnl) == (
        10,
        Money.of("-2.50"),
    )  # fmt: skip  (no gross profit yet; 2.50 of charges)
    snapshots = await rig.snapshots.find({"account_id": rig.account_id})
    assert len(snapshots) == 2 and snapshots[0].positions[0].instrument_id == INSTRUMENT


async def test_money_is_stored_as_decimal128_never_a_float(rig: PaperMongoRig) -> None:
    s = Session(rig.account_id)
    await write(rig, s.entries())

    order = await rig.database[Collection.PAPER_ORDERS].find_one({"ordertag": s.tag})
    execution = await rig.database[Collection.PAPER_EXECUTIONS].find_one(
        {"account_id": rig.account_id}
    )
    position = await rig.database[Collection.PAPER_POSITIONS].find_one(
        {"account_id": rig.account_id}
    )
    snapshot = await rig.database[Collection.PAPER_PORTFOLIO_SNAPSHOTS].find_one(
        {"account_id": rig.account_id}
    )

    assert order and execution and position and snapshot
    for value in (
        order["limit_price"], order["average_price"], execution["price"], execution["fees"],
        position["average_price"], position["realised_pnl"], snapshot["cash"],
        snapshot["positions"][0]["average_price"],
    ):  # fmt: skip
        assert isinstance(value, Decimal128)


async def test_a_session_can_be_reconstructed_from_persisted_state_alone(
    rig: PaperMongoRig,
) -> None:
    s = Session(rig.account_id)
    await write(rig, s.entries())

    restored = await rig.store.load(rig.account_id, DAY)

    (item,) = restored.orders
    assert item.order == s.order(S.FILLED, 10, 2) and (item.seq, item.trades) == (3, 2)
    assert [(f.trade.trade_id, f.trade.quantity, f.fees) for f in restored.fills] == [
        (f"{s.order_id}-1", 4, Money.of("1.50")), (f"{s.order_id}-2", 6, Money.of("2.00")),
    ]  # fmt: skip
    assert (
        restored.fills[0].trade == s.fill(1, 4, S.PARTIALLY_FILLED, 4).trade
    )  # to the microsecond
    empty = await rig.store.load(rig.account_id, "2026-09-19")
    assert empty.orders == [] and empty.fills == []


async def test_replaying_the_whole_session_duplicates_nothing(rig: PaperMongoRig) -> None:
    s = Session(rig.account_id)
    await write(rig, s.entries())

    await write(rig, s.entries())  # a redelivery: a retry, or a replay after a crash

    assert await rig.orders.count({"account_id": rig.account_id}) == 1
    assert await rig.executions.count({"account_id": rig.account_id}) == 2
    assert await rig.positions.count({"account_id": rig.account_id}) == 1
    assert await rig.snapshots.count({"account_id": rig.account_id}) == 2
    order = await rig.orders.get_by_ordertag(s.tag)
    assert order is not None and len(await rig.events.for_order(order.id)) == 3


class FlakyFills(FillApplier):
    """Fails its first `apply` the way a lost connection would, then behaves."""

    def __init__(self, inner: FillApplier) -> None:
        self._inner = inner
        self.failures = 1

    async def apply(self, fill: FillApplication) -> FillOutcome:
        if self.failures:
            self.failures -= 1
            raise OSError("connection reset")
        return await self._inner.apply(fill)


async def test_a_failed_write_stays_queued_and_the_retry_completes_without_duplicates(
    dev_settings: Any,
) -> None:
    mongo = MongoClientFactory(dev_settings)
    base = PaperMongoRig(mongo.client, mongo.database())
    rig = PaperMongoRig(mongo.client, mongo.database(), FlakyFills(base.fills))
    await rig.prepare()
    try:
        s = Session(rig.account_id)
        journal = rig.journal()
        for entry in s.entries():
            journal.append(entry)

        with pytest.raises(OSError):
            await journal.flush()
        assert (
            journal.pending == len(s.entries()) - 1
        )  # the placement got through; the fill did not
        await journal.flush()  # the retry

        assert journal.pending == 0
        assert await rig.executions.count({"account_id": rig.account_id}) == 2
        order = await rig.orders.get_by_ordertag(s.tag)
        assert order is not None and [e.seq for e in await rig.events.for_order(order.id)] == [
            1,
            2,
            3,
        ]
    finally:
        await rig.cleanup()
        await mongo.close()


async def test_an_order_the_engine_already_wrote_is_adopted_not_duplicated(
    rig: PaperMongoRig,
) -> None:
    """Decision 7: the engine persists the intent BEFORE calling the broker. The paper broker must
    update that document, keeping its key and creation time, not insert a second one."""
    s = Session(rig.account_id)
    intent = OrderRecord(
        _id=f"engine-{s.order_id}", idempotency_key=f"engine-key-{s.order_id}", ordertag=s.tag,
        instrument_id=INSTRUMENT, side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=10,
        limit_price=Money.of("100.00"), state="PENDING_NEW", session_date=DAY,
        created_at=T0 - timedelta(seconds=5), updated_at=T0 - timedelta(seconds=5),
        account_id=rig.account_id,
    )  # fmt: skip
    await rig.orders.insert(intent)

    await write(rig, s.entries())

    (order,) = await rig.orders.find({"account_id": rig.account_id})
    assert order.id == intent.id and order.idempotency_key == intent.idempotency_key
    assert (order.state, order.created_at) == ("FILLED", intent.created_at)
    assert len(await rig.events.for_order(order.id)) == 3


async def test_a_fill_for_an_order_the_store_has_never_seen_still_lands(rig: PaperMongoRig) -> None:
    s = Session(rig.account_id)

    await write(rig, [s.fill(1, 4, S.PARTIALLY_FILLED, 4)])  # the placement entry never arrived

    assert await rig.orders.count({"account_id": rig.account_id}) == 1
    assert await rig.executions.count({"account_id": rig.account_id}) == 1


async def test_an_existing_position_row_is_updated_in_place_under_its_own_id(
    rig: PaperMongoRig,
) -> None:
    held = PositionRecord(
        _id=f"other-writer-{rig.account_id}", account_id=rig.account_id, instrument_id=INSTRUMENT,
        net_quantity=0, average_price=Money.zero(), realised_pnl=Money.zero(), updated_at=T0,
    )  # fmt: skip
    await rig.positions.insert(held)

    await write(rig, Session(rig.account_id).entries())

    (position,) = await rig.positions.find({"account_id": rig.account_id})
    assert position.id == held.id and position.net_quantity == 10
