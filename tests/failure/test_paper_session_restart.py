"""EM-64 / plan §18: restart and fault scenarios for a paper session persisted on real Atlas.

The broker is driven the way the execution engine will drive it (place, cancel, look up by tag),
over the real journal and store. No real broker is involved: paper needs no order access."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from emporos.broker.errors import BrokerRejectedError, BrokerTransportError
from emporos.broker.models import BrokerOrderStatus, MarketDataMode
from emporos.broker.paper.account import PaperAccount
from emporos.broker.paper.broker import PaperBroker
from emporos.broker.paper.costs import ScheduledCosts
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.faults import ScriptedReplyLoss
from emporos.broker.paper.fills import ParticipationLiquidity
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.fees import IntradayCharges
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.persistence.mongo import MongoClientFactory
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from tests.support.fakes import make_tick
from tests.support.paper_market import NOW, FakeMarketData
from tests.support.paper_mongo import PaperMongoRig
from tests.support.paper_rig import ID, SBIN, order

pytestmark = [pytest.mark.integration, pytest.mark.failure]
DAY = "2026-09-18"
S = BrokerOrderStatus


class World:
    """One paper account on real Mongo that can be 'restarted': a fresh broker, same store."""

    def __init__(self, rig: PaperMongoRig) -> None:
        self.rig = rig
        self.replies = ScriptedReplyLoss()
        self.clock = FixedClock(NOW)
        self.market: FakeMarketData
        self.broker: PaperBroker
        self.journal = rig.journal()
        self._volume = 1_000_000
        self._seq = 0

    async def start(self, *, restore: bool) -> None:
        """A process start: with `restore`, resume whatever the store holds for today."""
        restored = await self.rig.store.load(self.rig.account_id, DAY) if restore else None
        self.market = FakeMarketData([SBIN], [])
        self.journal = self.rig.journal()  # a new process has a new (empty) write queue
        schedule = FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21))
        factory = PaperBrokerFactory(
            PaperBrokerConfig(self.rig.account_id, Money.of("1000000")),
            ScheduledCosts(IntradayCharges(schedule)),
            liquidity=ParticipationLiquidity(Decimal(1)),
            replies=self.replies,
        )
        self.broker = factory.build(self.market, self.journal, self.clock, IdGenerator(), restored)
        await self.broker.subscribe_market_data([ID], MarketDataMode.QUOTE)
        self.tape("100.00")  # baseline reading

    def tape(self, price: str, shares: int = 0) -> None:
        self._volume += shares
        self._seq += 1
        self.market.emit(
            make_tick(
                self.clock.now(), price, volume=self._volume, instrument_id=ID, sequence=self._seq
            )
        )


@pytest.fixture
async def world(dev_settings: Any) -> AsyncIterator[World]:
    mongo = MongoClientFactory(dev_settings)
    rig = PaperMongoRig(mongo.client, mongo.database())
    await rig.prepare()
    try:
        yield World(rig)
    finally:
        await rig.cleanup()
        await mongo.close()


async def persisted_matches_memory(w: World) -> None:
    """The acceptance test in one place: rebuild the session from persisted state ALONE and
    compare it with what the live broker says."""
    orders = await w.rig.orders.for_account_session(w.rig.account_id, DAY)
    executions = await w.rig.executions.for_account_session(w.rig.account_id, DAY)
    replay = PaperAccount(Money.of("1000000"))
    restored = await w.rig.store.load(w.rig.account_id, DAY)
    for fill in restored.fills:
        replay.apply(fill.trade, fill.fees)

    live_book = await w.broker.get_order_book()
    assert sorted((o.broker_order_id, o.status, o.filled_quantity) for o in live_book) == sorted(
        (o.broker_order_id or o.id, BrokerOrderStatus(o.state), o.filled_quantity) for o in orders
    )
    assert [t.trade_id for t in await w.broker.get_trade_book()] == [
        x.broker_trade_id for x in executions
    ]
    for position in await w.rig.positions.open_for_account(w.rig.account_id):
        rebuilt = replay.position(position.instrument_id)
        assert rebuilt is not None
        assert (position.net_quantity, position.average_price, position.realised_pnl) == (
            rebuilt.net_quantity, rebuilt.average_price, rebuilt.realised,
        )  # fmt: skip
    for record in orders:  # every order's event log is contiguous from 1 and ends in its state
        events = await w.rig.events.for_order(record.id)
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert events[-1].state == record.state


async def test_a_session_survives_a_restart_with_an_open_order_and_completes_exactly_once(
    world: World,
) -> None:
    await world.start(restore=False)
    first = await world.broker.place_order(order("RST0001", qty=10, price="100.00"))
    world.tape("99.00", shares=4)  # 4 of 10 filled
    await world.broker.flush_journal()
    await persisted_matches_memory(world)

    await world.start(restore=True)  # the worker restarts with the order still open

    (resumed,) = await world.broker.find_orders_by_tag("RST0001")
    assert (resumed.broker_order_id, resumed.status, resumed.filled_quantity) == (
        first.broker_order_id, S.PARTIALLY_FILLED, 4,
    )  # fmt: skip
    with pytest.raises(BrokerRejectedError, match="already in use"):
        await world.broker.place_order(order("RST0001", qty=10))  # a resend is refused
    world.tape("99.00", shares=6)  # the rest fills after the restart
    await world.broker.flush_journal()

    assert len(await world.rig.executions.find({"account_id": world.rig.account_id})) == 2
    assert await world.rig.orders.count({"account_id": world.rig.account_id}) == 1
    (position,) = await world.broker.get_positions()
    assert position.net_quantity == 10
    await persisted_matches_memory(world)


async def test_a_round_trip_pays_real_charges_and_the_persisted_pnl_matches(world: World) -> None:
    await world.start(restore=False)
    await world.broker.place_order(order("RT00001", qty=100, price="500.00"))
    world.tape("500.00", shares=100)
    await world.broker.place_order(order("RT00002", OrderSide.SELL, qty=100, price="510.00"))
    world.tape("510.00", shares=100)
    await world.broker.flush_journal()

    (position,) = await world.broker.get_positions()
    assert position.net_quantity == 0
    assert position.realized_pnl == Money.of("1000.00") - Money.of("26.96") - Money.of("38.26")
    stored = await world.rig.positions.get_for(world.rig.account_id, ID)
    assert stored is not None and stored.realised_pnl == position.realized_pnl
    await persisted_matches_memory(world)


async def test_a_crash_before_the_fill_was_flushed_loses_it_but_never_duplicates_it(
    world: World,
) -> None:
    """The write-behind window, stated honestly: a fill that was applied in memory but not yet
    flushed dies with the process. The order is still OPEN on disk, so live data re-fills it —
    once. Nothing is ever doubled."""
    await world.start(restore=False)
    await world.broker.place_order(order("CRS0001", qty=10, price="100.00"))  # durable: acked
    world.tape("99.00", shares=10)  # filled in memory...
    assert world.journal.pending > 0  # ...but still only queued: the process dies here

    await world.start(restore=True)
    (after,) = await world.broker.find_orders_by_tag("CRS0001")
    assert (after.status, after.filled_quantity) == (
        S.OPEN,
        0,
    )  # the fill was lost with the process
    world.tape("99.00", shares=10)  # the market trades again: the order fills, once
    await world.broker.flush_journal()

    assert len(await world.rig.executions.find({"account_id": world.rig.account_id})) == 1
    await persisted_matches_memory(world)


async def test_a_lost_reply_leaves_a_durable_order_the_tag_finds_after_a_restart(
    world: World,
) -> None:
    await world.start(restore=False)
    world.replies.lose_next()

    with pytest.raises(BrokerTransportError):
        await world.broker.place_order(order("LST0001", qty=5, price="100.00"))
    await world.start(restore=True)  # the engine restarts before it can resolve the tag

    (found,) = await world.broker.find_orders_by_tag("LST0001")
    assert found.status is S.OPEN and found.quantity == 5
    with pytest.raises(BrokerRejectedError, match="already in use"):
        await world.broker.place_order(order("LST0001", qty=5, price="100.00"))
    assert await world.rig.orders.count({"account_id": world.rig.account_id}) == 1
    await persisted_matches_memory(world)


async def test_an_order_rejected_downstream_and_a_cancel_are_both_reconstructable(
    world: World,
) -> None:
    await world.start(restore=False)
    live = await world.broker.place_order(order("CXL0001", qty=10, price="100.00"))
    await world.broker.place_order(order("BIG0001", qty=100_000_000, price="100.00"))  # no funds
    from emporos.broker.models import CancelOrderRequest
    from emporos.domain.orders import OrderType

    await world.broker.cancel_order(CancelOrderRequest(live.broker_order_id, OrderType.LIMIT))
    await world.broker.flush_journal()

    states = {o.client_tag: o.status for o in await world.broker.get_order_book()}
    assert states == {"CXL0001": S.CANCELLED, "BIG0001": S.REJECTED}
    await persisted_matches_memory(world)
    await world.start(restore=True)
    assert {o.client_tag: o.status for o in await world.broker.get_order_book()} == states
