"""`PaperBroker`'s choreography: what it journals, publishes and refuses, and in what order."""

from __future__ import annotations

import inspect
import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.broker.errors import BrokerRejectedError, BrokerTransportError
from emporos.broker.models import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerOrderUpdate,
    BrokerPosition,
    CancelOrderRequest,
    MarketDataMode,
    ModifyOrderRequest,
    ProductType,
)
from emporos.broker.paper.chance import SeededChance
from emporos.broker.paper.exchange import RestoredOrder
from emporos.broker.paper.faults import RandomReplyLoss, ScriptedReplyLoss
from emporos.broker.paper.fills import FullLiquidity, ParticipationLiquidity
from emporos.broker.paper.journal import (
    FillRecorded,
    JournalEntry,
    OrderStateRecorded,
    RestoredFill,
    RestoredSession,
    SnapshotRecorded,
)
from emporos.broker.paper.market import MarketDataSource
from emporos.core.errors import ErrorClassification
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.ticks import Tick
from tests.support.paper_rig import ID, FlatCosts, PaperRig, build_rig, order

S = BrokerOrderStatus


def rig(**kw: object) -> PaperRig:
    kw.setdefault("liquidity", FullLiquidity())
    return build_rig(**kw)  # type: ignore[arg-type]


def cancel(order_id: str) -> CancelOrderRequest:
    return CancelOrderRequest(order_id, OrderType.LIMIT)


# ---- structure -------------------------------------------------------------------------------


def test_the_market_data_protocol_has_no_way_to_reach_an_order_endpoint() -> None:
    members = {name for name, _ in inspect.getmembers(MarketDataSource) if not name.startswith("_")}

    assert members == {
        "get_instruments", "get_quote", "get_historical_candles",
        "subscribe_market_data", "unsubscribe_market_data", "on_tick",
    }  # fmt: skip
    assert not any(word in name for name in members for word in ("order", "trade", "position"))


# ---- placement -------------------------------------------------------------------------------


async def test_placement_journals_the_order_and_flushes_before_it_answers() -> None:
    r = rig()

    ack = await r.broker.place_order(order("PLC001"))

    (entry,) = r.journal.of(OrderStateRecorded)
    assert isinstance(entry, OrderStateRecorded)
    assert (entry.order.broker_order_id, entry.seq, entry.order.status) == (
        ack.broker_order_id, 1, S.OPEN,
    )  # fmt: skip
    assert (entry.account_id, entry.session_date) == ("PAPER01", "2026-09-18")
    assert r.journal.flushes == 1


async def test_a_second_order_with_a_tag_in_use_is_refused_never_duplicated() -> None:
    r = rig()
    await r.broker.place_order(order("DUP001"))

    with pytest.raises(BrokerRejectedError, match="already in use") as raised:
        await r.broker.place_order(order("DUP001"))

    assert raised.value.classification is ErrorClassification.DEFINITIVE
    assert len(await r.broker.get_order_book()) == 1


async def test_a_request_the_api_would_refuse_raises_and_leaves_no_trace() -> None:
    r = rig()

    with pytest.raises(BrokerRejectedError, match="tick size"):
        await r.broker.place_order(order("BAD001", price="100.03"))
    with pytest.raises(BrokerRejectedError, match="DELIVERY"):
        await r.broker.place_order(order("BAD002", product=ProductType.DELIVERY))

    assert await r.broker.get_order_book() == [] and r.journal.entries == []


async def test_an_order_the_exchange_refuses_is_acked_then_recorded_as_rejected_and_pushed() -> (
    None
):
    r = rig(cash="500")
    updates: list[BrokerOrderUpdate] = []
    r.broker.on_order_update(updates.append)

    ack = await r.broker.place_order(order("RMS001", qty=10, price="100.00"))  # needs 1000

    (rejected,) = await r.broker.find_orders_by_tag("RMS001")
    assert ack.broker_order_id == rejected.broker_order_id
    assert rejected.status is S.REJECTED and "insufficient funds" in rejected.status_message
    assert [u.order.status for u in updates] == [S.REJECTED]
    (entry,) = r.journal.of(OrderStateRecorded)
    assert isinstance(entry, OrderStateRecorded) and "insufficient" in entry.reason
    r.tape("90.00")  # a rejected order can never trade
    assert await r.broker.get_trade_book() == []


async def test_working_orders_reserve_funds_so_two_orders_cannot_spend_the_same_cash() -> None:
    r = rig(cash="1500")

    await r.broker.place_order(order("ONE001", qty=10, price="100.00"))  # reserves 1000
    await r.broker.place_order(order("TWO001", qty=10, price="100.00"))  # 500 left: refused

    assert [o.status for o in await r.broker.get_order_book()] == [S.OPEN, S.REJECTED]


async def test_an_unsubscribed_instrument_is_warned_about_because_it_can_never_fill(
    caplog: pytest.LogCaptureFixture,
) -> None:
    r = rig()

    with caplog.at_level(logging.WARNING):
        await r.broker.place_order(order("SUB001"))
    assert "not subscribed" in caplog.text

    caplog.clear()
    await r.broker.subscribe_market_data([ID], MarketDataMode.QUOTE)
    with caplog.at_level(logging.WARNING):
        await r.broker.place_order(order("SUB002"))
    assert caplog.text == ""
    await r.broker.unsubscribe_market_data([ID])
    assert r.market.subscribed == set()


# ---- ambiguity -------------------------------------------------------------------------------


async def test_a_lost_reply_leaves_a_journaled_live_order_that_only_the_tag_can_find() -> None:
    replies = ScriptedReplyLoss()
    r = rig(replies=replies)
    replies.lose_next()

    with pytest.raises(BrokerTransportError) as raised:
        await r.broker.place_order(order("LOST01"))

    assert raised.value.classification is ErrorClassification.AMBIGUOUS
    (found,) = await r.broker.find_orders_by_tag("LOST01")
    assert found.status is S.OPEN and len(r.journal.of(OrderStateRecorded)) == 1  # durable, too
    r.tape("99.00")
    assert (await r.broker.find_orders_by_tag("LOST01"))[0].status is S.FILLED  # and it is live


async def test_random_reply_loss_is_reproducible_from_a_seed() -> None:
    async def losses(seed: int) -> list[bool]:
        r = rig(replies=RandomReplyLoss(Decimal("0.5"), SeededChance(seed)))
        outcome = []
        for n in range(20):
            try:
                await r.broker.place_order(order(f"RND{n:03d}", qty=1))
                outcome.append(False)
            except BrokerTransportError:
                outcome.append(True)
        return outcome

    assert await losses(11) == await losses(11)
    assert 3 < sum(await losses(11)) < 17


async def test_a_journal_that_cannot_be_written_makes_placement_ambiguous_not_failed() -> None:
    r = rig()
    r.journal.fail_next_flush = True

    with pytest.raises(BrokerTransportError, match="journal") as raised:
        await r.broker.place_order(order("JRN001"))

    assert raised.value.classification is ErrorClassification.AMBIGUOUS
    assert len(await r.broker.find_orders_by_tag("JRN001")) == 1  # exists: resolve, never resend


async def test_a_journal_that_cannot_be_written_makes_a_cancel_ambiguous_too() -> None:
    r = rig()
    ack = await r.broker.place_order(order("JRN002"))
    r.journal.fail_next_flush = True

    with pytest.raises(BrokerTransportError):
        await r.broker.cancel_order(cancel(ack.broker_order_id))


# ---- fills -----------------------------------------------------------------------------------


async def test_a_fill_updates_book_positions_funds_journal_and_pushes_the_update() -> None:
    r = rig(costs=FlatCosts("2.00"))
    updates: list[BrokerOrderUpdate] = []
    r.broker.on_order_update(updates.append)
    ack = await r.broker.place_order(order("FIL001", qty=10, price="100.00"))

    r.tape("99.00")

    (trade,) = await r.broker.get_trade_book()
    assert (trade.trade_id, trade.quantity, trade.price) == (
        f"{ack.broker_order_id}-1", 10, Money.of("100.00"),
    )  # fmt: skip
    (position,) = await r.broker.get_positions()
    assert (position.net_quantity, position.average_price, position.ltp) == (
        10, Money.of("100.00"), Money.of("99.00"),
    )  # fmt: skip
    assert position.unrealized_pnl == Money.of("-10.00") and position.realized_pnl == Money.of(
        "-2.00"
    )
    funds = await r.broker.get_funds()
    assert funds.realized_pnl == Money.of("-2.00") and funds.utilised == Money.of("1000.00")
    assert [u.order.status for u in updates] == [S.OPEN, S.FILLED]
    (fill,) = r.journal.of(FillRecorded)
    assert isinstance(fill, FillRecorded)
    assert (fill.trade, fill.fees, fill.seq) == (trade, Money.of("2.00"), 2)
    assert fill.position.net_quantity == 10
    (snapshot,) = r.journal.of(SnapshotRecorded)
    assert isinstance(snapshot, SnapshotRecorded) and snapshot.realised == Money.of("-2.00")


async def test_a_round_trip_books_profit_after_charges() -> None:
    r = rig(costs=FlatCosts("2.00"))
    await r.broker.place_order(order("BUY001", qty=10, price="100.00"))
    r.tape("100.00")
    await r.broker.place_order(order("SEL001", OrderSide.SELL, qty=10, price="101.00"))
    r.tape("101.50")

    (position,) = await r.broker.get_positions()
    funds = await r.broker.get_funds()

    assert position.net_quantity == 0  # closed positions stay listed, as the real broker does
    assert position.realized_pnl == Money.of("6.00")  # 10 gross - 2 x 2.00 charges
    assert funds.net == Money.of("1000006.00") and funds.realized_pnl == Money.of("6.00")


async def test_a_fill_lands_before_any_handler_sees_the_tick_that_caused_it() -> None:
    r = rig()
    updates: list[BrokerOrderUpdate] = []
    r.broker.on_order_update(updates.append)
    seen: list[tuple[str, int]] = []
    r.broker.on_tick(lambda t: seen.append((str(t.ltp.amount), len(updates))))
    await r.broker.place_order(order("ORD001"))  # publishes its own OPEN update first

    r.tape("99.00")

    assert seen[-1] == ("99.00", 2)  # OPEN and FILLED were both out before the handler ran


async def test_a_defect_in_fill_simulation_never_silences_the_tick_stream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        def execution_price(self, order: object, tick: object) -> Money:
            raise RuntimeError("policy bug")

    r = rig(fill_policy=Broken())
    seen: list[Tick] = []
    r.broker.on_tick(seen.append)
    await r.broker.place_order(order("BRK001"))

    with caplog.at_level(logging.ERROR):
        r.tape("99.00")

    assert len(seen) == 1 and "fill simulation failed" in caplog.text


async def test_ticks_flow_to_handlers_even_with_no_orders() -> None:
    r = rig(baseline=False)
    seen: list[Tick] = []
    r.broker.on_tick(seen.append)

    r.tape("100.00")

    assert len(seen) == 1


async def test_partial_fills_follow_the_participation_model_end_to_end() -> None:
    r = rig(liquidity=ParticipationLiquidity(Decimal("0.5")))
    ack = await r.broker.place_order(order("PRT001", qty=10))

    r.tape("99.00", shares=8)  # budget 4
    (partial,) = await r.broker.find_orders_by_tag("PRT001")
    r.tape("99.00", shares=40)  # budget 20: the remaining 6 fill
    (done,) = await r.broker.find_orders_by_tag("PRT001")

    assert (partial.status, partial.filled_quantity) == (S.PARTIALLY_FILLED, 4)
    assert (done.status, done.filled_quantity) == (S.FILLED, 10)
    assert [t.quantity for t in await r.broker.get_trade_book()] == [4, 6]
    assert {t.broker_order_id for t in await r.broker.get_trade_book()} == {ack.broker_order_id}


async def test_latency_keeps_an_order_off_the_tape_until_it_has_reached_the_exchange() -> None:
    r = rig(latency=timedelta(seconds=3))
    await r.broker.place_order(order("LAT001"))

    r.advance(1)
    r.tape("90.00")
    assert (await r.broker.find_orders_by_tag("LAT001"))[0].status is S.OPEN
    r.advance(2)
    r.tape("90.00")
    assert (await r.broker.find_orders_by_tag("LAT001"))[0].status is S.FILLED


# ---- cancel and modify -----------------------------------------------------------------------


async def test_cancelling_is_journaled_and_a_filled_order_cannot_be_cancelled() -> None:
    r = rig()
    a = await r.broker.place_order(order("CXL001"))
    b = await r.broker.place_order(order("CXL002", price="50.00"))
    r.tape("99.00")  # fills the 100.00 buy, not the 50.00 one

    with pytest.raises(BrokerRejectedError, match="FILLED"):
        await r.broker.cancel_order(cancel(a.broker_order_id))
    await r.broker.cancel_order(cancel(b.broker_order_id))

    entries = r.journal.of(OrderStateRecorded)
    assert [
        (e.order.client_tag, e.seq, e.order.status)
        for e in entries
        if isinstance(e, OrderStateRecorded)
    ][-1] == ("CXL002", 2, S.CANCELLED)
    r.tape("40.00")
    assert await r.broker.find_orders_by_tag("CXL002") == [
        o for o in await r.broker.get_order_book() if o.client_tag == "CXL002"
    ]
    assert (await r.broker.find_orders_by_tag("CXL002"))[0].filled_quantity == 0


async def test_a_modification_is_validated_like_a_new_request_and_journaled() -> None:
    r = rig()
    ack = await r.broker.place_order(order("MOD001", qty=10, price="100.00"))

    with pytest.raises(BrokerRejectedError, match="tick size"):
        await r.broker.modify_order(
            ModifyOrderRequest(ack.broker_order_id, ID, OrderType.LIMIT, 10, Money.of("99.03"))
        )
    await r.broker.modify_order(
        ModifyOrderRequest(ack.broker_order_id, ID, OrderType.LIMIT, 5, Money.of("99.00"))
    )

    (modified,) = await r.broker.find_orders_by_tag("MOD001")
    assert (modified.quantity, modified.price) == (5, Money.of("99.00"))
    assert [e.seq for e in r.journal.entries if isinstance(e, OrderStateRecorded)] == [1, 2]
    with pytest.raises(BrokerRejectedError, match="unknown order"):
        await r.broker.modify_order(
            ModifyOrderRequest("nope", ID, OrderType.LIMIT, 1, Money.of("1"))
        )


# ---- session and account surface -------------------------------------------------------------


async def test_the_session_expires_and_is_renewed_and_there_are_never_holdings() -> None:
    r = rig()
    first = await r.broker.ensure_session()

    r.advance(13 * 3600)
    second = await r.broker.ensure_session()

    assert second.established_at > first.established_at
    assert await r.broker.get_holdings() == []
    assert (await r.broker.get_profile()).client_code == "PAPER01"


async def test_flush_and_snapshot_are_available_for_a_cadence_and_for_shutdown() -> None:
    r = rig()
    r.broker.record_snapshot()
    await r.broker.flush_journal()

    (snapshot,) = r.journal.of(SnapshotRecorded)
    assert isinstance(snapshot, SnapshotRecorded) and snapshot.cash == Money.of("1000000")
    assert r.journal.flushes == 1


# ---- restart ---------------------------------------------------------------------------------


def persisted(entries: list[JournalEntry]) -> RestoredSession:
    """What a store would hand back: the latest state of each order and every fill."""
    latest: dict[str, tuple[BrokerOrder, int]] = {}
    fills: list[RestoredFill] = []
    trades: dict[str, int] = {}
    for entry in entries:
        if isinstance(entry, OrderStateRecorded | FillRecorded):
            latest[entry.order.broker_order_id] = (entry.order, entry.seq)
        if isinstance(entry, FillRecorded):
            fills.append(RestoredFill(entry.trade, entry.fees))
            trades[entry.order.broker_order_id] = trades.get(entry.order.broker_order_id, 0) + 1
    return RestoredSession(
        [RestoredOrder(o, s, trades.get(i, 0)) for i, (o, s) in latest.items()], fills
    )


async def test_a_restarted_broker_resumes_the_session_without_duplicating_orders_or_fills() -> None:
    before = rig(liquidity=ParticipationLiquidity(Decimal(1)), costs=FlatCosts("1.00"))
    await before.broker.place_order(order("RST001", qty=10))
    await before.broker.place_order(order("RST002", qty=5, price="50.00"))
    before.tape("99.00", shares=4)  # RST001 part-filled: 4 of 10
    book, positions, funds = (
        await before.broker.get_order_book(),
        await before.broker.get_positions(),
        await before.broker.get_funds(),
    )

    after = rig(
        liquidity=ParticipationLiquidity(Decimal(1)),
        costs=FlatCosts("1.00"),
        restored=persisted(before.journal.entries),
    )

    assert await after.broker.get_order_book() == book

    def held(positions: list[BrokerPosition]) -> list[tuple[str, int, Money, Money | None]]:
        return [
            (p.instrument_id, p.net_quantity, p.average_price, p.realized_pnl) for p in positions
        ]

    assert held(await after.broker.get_positions()) == held(positions)
    assert (await after.broker.get_funds()).realized_pnl == funds.realized_pnl
    with pytest.raises(BrokerRejectedError, match="already in use"):
        await after.broker.place_order(order("RST001", qty=10))  # the engine may not resend
    after.tape("99.00", shares=6)  # the remaining 6 fill: trade numbering continues at -2
    trades = await after.broker.get_trade_book()
    assert [t.trade_id.rsplit("-", 1)[1] for t in trades] == ["1", "2"]
    assert sum(t.quantity for t in trades) == 10
