"""The simulated exchange: matching against ticks, and the order lifecycle it owns."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import BrokerOrder, BrokerOrderStatus, PlaceOrderRequest
from emporos.broker.paper.exchange import ExchangeEvent, RestoredOrder, SimulatedExchange
from emporos.broker.paper.fills import (
    AtLimitPrice,
    FullLiquidity,
    LimitFillPolicy,
    LiquidityModel,
    ParticipationLiquidity,
    StopTrigger,
    TouchCrossing,
)
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from tests.support.fakes import make_tick
from tests.support.paper_market import NOW
from tests.support.paper_rig import ID

S = BrokerOrderStatus


def exchange(
    liquidity: LiquidityModel | None = None, latency: timedelta = timedelta(0)
) -> SimulatedExchange:
    return SimulatedExchange(
        LimitFillPolicy(TouchCrossing(), AtLimitPrice()),
        liquidity or FullLiquidity(),
        StopTrigger(),
        IdGenerator(),
        latency,
    )


def request(
    tag: str, side: OrderSide = OrderSide.BUY, qty: int = 10, price: str = "100.00"
) -> PlaceOrderRequest:
    return PlaceOrderRequest(ID, side, OrderType.LIMIT, qty, Money.of(price), tag)


def stop(tag: str, side: OrderSide, price: str, trigger: str, qty: int = 10) -> PlaceOrderRequest:
    return PlaceOrderRequest(
        ID,
        side,
        OrderType.STOPLOSS_LIMIT,
        qty,
        Money.of(price),
        tag,
        trigger_price=Money.of(trigger),
    )


def at(price: str, volume: int | None = None, seconds: int = 0, **kw: object):  # type: ignore[no-untyped-def]
    return make_tick(NOW + timedelta(seconds=seconds), price, volume=volume, **kw)  # type: ignore[arg-type]


def now(seconds: int = 0):  # type: ignore[no-untyped-def]
    return NOW + timedelta(seconds=seconds)


# ---- price side: a fill needs the tape to trade through the limit --------------------------


@pytest.mark.parametrize(
    ("side", "price", "fills"),
    [
        (OrderSide.BUY, "100.05", False),  # above a buy limit: the wrong side
        (OrderSide.BUY, "100.00", True),
        (OrderSide.BUY, "99.90", True),
        (OrderSide.SELL, "99.95", False),  # below a sell limit: the wrong side
        (OrderSide.SELL, "100.00", True),
        (OrderSide.SELL, "100.10", True),
    ],
)
def test_an_order_fills_only_when_a_tick_trades_at_or_through_its_limit(
    side: OrderSide, price: str, fills: bool
) -> None:
    ex = exchange()
    ex.submit(request("A", side), NOW)

    events = ex.on_tick(at(price), NOW)

    assert [e.order.status for e in events] == ([S.FILLED] if fills else [])


def test_a_marketable_order_does_not_fill_on_placement_only_on_a_later_tick() -> None:
    ex = exchange()
    ex.on_tick(at("99.00"), NOW)  # the market is already through a buy at 100...
    ex.submit(request("A", price="100.00"), NOW)

    assert ex.working_orders()[0].filled_quantity == 0  # ...but nothing filled at placement
    assert ex.on_tick(at("99.00", seconds=1), now(1))[0].order.status is S.FILLED


def test_ticks_flagged_out_of_order_never_fill_anything() -> None:
    ex = exchange()
    ex.submit(request("A"), NOW)

    assert ex.on_tick(at("90.00", out_of_order=True), NOW) == []


def test_other_instruments_ticks_do_not_touch_an_order() -> None:
    ex = exchange()
    ex.submit(request("A"), NOW)

    assert ex.on_tick(at("90.00", instrument_id="NSE:2885"), NOW) == []


def test_an_order_is_deaf_to_ticks_that_arrive_before_its_latency_has_elapsed() -> None:
    ex = exchange(latency=timedelta(seconds=2))
    ex.submit(request("A"), NOW)

    assert ex.on_tick(at("90.00", seconds=1), now(1)) == []  # still in flight to the exchange
    assert ex.on_tick(at("90.00", seconds=2), now(2))[0].order.status is S.FILLED


def test_a_negative_latency_is_refused() -> None:
    with pytest.raises(ValueError, match="latency"):
        exchange(latency=timedelta(seconds=-1))


# ---- quantity side: partial fills and shared liquidity ---------------------------------------


def test_participation_turns_traded_volume_into_partial_fills_until_filled() -> None:
    ex = exchange(ParticipationLiquidity(Decimal("0.5")))
    ex.submit(request("A", qty=10), NOW)
    ex.on_tick(at("100.00", volume=1000), NOW)  # baseline reading: nothing can be measured yet

    first = ex.on_tick(at("100.00", volume=1012, seconds=1), now(1))  # 12 traded -> budget 6
    second = ex.on_tick(at("100.00", volume=1032, seconds=2), now(2))  # 20 traded -> 10, need 4

    assert [(e.order.status, e.order.filled_quantity) for e in first] == [(S.PARTIALLY_FILLED, 6)]
    assert [(e.order.status, e.order.filled_quantity) for e in second] == [(S.FILLED, 10)]
    assert first[0].trade is not None and first[0].trade.quantity == 6
    assert second[0].trade is not None and second[0].trade.quantity == 4


def test_the_first_tick_and_a_feed_without_volume_fill_nothing_under_participation() -> None:
    ex = exchange(ParticipationLiquidity(Decimal(1)))
    ex.submit(request("A"), NOW)

    assert ex.on_tick(at("100.00", volume=1000), NOW) == []  # no baseline yet
    assert ex.on_tick(at("100.00", volume=None, seconds=1), now(1)) == []  # feed gives no volume


def test_a_falling_volume_counter_is_a_glitch_not_negative_liquidity() -> None:
    ex = exchange(ParticipationLiquidity(Decimal(1)))
    ex.submit(request("A"), NOW)
    ex.on_tick(at("100.00", volume=1000), NOW)

    assert ex.on_tick(at("100.00", volume=900, seconds=1), now(1)) == []
    assert ex.on_tick(at("100.00", volume=910, seconds=2), now(2))[0].order.filled_quantity == 10


def test_orders_share_a_ticks_liquidity_in_time_priority() -> None:
    ex = exchange(ParticipationLiquidity(Decimal(1)))
    ex.submit(request("FIRST", qty=8), NOW)
    ex.submit(request("SECOND", qty=8), NOW)
    ex.on_tick(at("100.00", volume=1000), NOW)

    events = ex.on_tick(at("100.00", volume=1010, seconds=1), now(1))  # only 10 shares traded

    assert [(e.order.client_tag, e.order.filled_quantity) for e in events] == [
        ("FIRST", 8),
        ("SECOND", 2),  # the earlier order took its 8 first; the later one got what was left
    ]


def test_the_average_price_weights_each_fill_and_trade_ids_are_deterministic() -> None:
    ex = exchange(ParticipationLiquidity(Decimal(1)))
    (placed,) = [ex.submit(request("A", qty=10), NOW)]
    ex.on_tick(at("100.00", volume=1000), NOW)

    a = ex.on_tick(at("99.00", volume=1004, seconds=1), now(1))[0]
    b = ex.on_tick(at("98.00", volume=1010, seconds=2), now(2))[0]

    assert a.trade is not None and b.trade is not None
    assert (a.trade.trade_id, b.trade.trade_id) == (
        f"{placed.order.broker_order_id}-1",
        f"{placed.order.broker_order_id}-2",
    )
    assert b.order.average_price == Money.of("100.00")  # AtLimitPrice: both fills at 100.00


# ---- stop-loss limit -------------------------------------------------------------------------


def test_a_stop_order_needs_its_trigger_first_and_the_triggering_tick_does_not_fill_it() -> None:
    ex = exchange()
    placed = ex.submit(stop("SL", OrderSide.SELL, price="99.00", trigger="100.00"), NOW)
    assert placed.order.status is S.TRIGGER_PENDING

    # A price above the trigger would satisfy the 99.00 sell limit, yet the trigger has not fired.
    assert ex.on_tick(at("101.00"), NOW) == []
    triggered = ex.on_tick(at("99.50", seconds=1), now(1))  # falls through the trigger
    assert [e.order.status for e in triggered] == [S.OPEN] and triggered[0].trade is None
    filled = ex.on_tick(at("99.00", seconds=2), now(2))  # the next tick may fill the limit order
    assert [e.order.status for e in filled] == [S.FILLED]


def test_a_buy_stop_triggers_upward() -> None:
    ex = exchange()
    ex.submit(stop("SL", OrderSide.BUY, price="101.00", trigger="100.50"), NOW)

    assert ex.on_tick(at("100.00"), NOW) == []
    assert ex.on_tick(at("100.50", seconds=1), now(1))[0].order.status is S.OPEN


# ---- lifecycle -------------------------------------------------------------------------------


def test_every_change_carries_a_monotonic_per_order_sequence() -> None:
    ex = exchange(ParticipationLiquidity(Decimal(1)))
    seqs: list[int] = []
    seqs.append(ex.submit(request("A", qty=10), NOW).seq)
    ex.on_tick(at("100.00", volume=1000), NOW)
    seqs += [e.seq for e in ex.on_tick(at("100.00", volume=1004, seconds=1), now(1))]
    seqs += [e.seq for e in ex.on_tick(at("100.00", volume=1010, seconds=2), now(2))]

    assert seqs == [1, 2, 3]


def test_cancelling_and_amending_a_working_order() -> None:
    ex = exchange()
    a = ex.submit(request("A", qty=10), NOW).order.broker_order_id
    b = ex.submit(request("B", qty=10), NOW).order.broker_order_id

    amended = ex.amend(a, now(1), 4, Money.of("99.00"), None)
    cancelled = ex.cancel(b, now(1))

    assert (amended.order.quantity, amended.order.price) == (4, Money.of("99.00"))
    assert cancelled.order.status is S.CANCELLED
    assert [o.client_tag for o in ex.working_orders()] == ["A"]


def test_a_terminal_order_refuses_every_change() -> None:
    ex = exchange()
    order_id = ex.submit(request("A"), NOW).order.broker_order_id
    ex.on_tick(at("100.00"), NOW)  # filled

    for action in (
        lambda: ex.cancel(order_id, now(1)),
        lambda: ex.amend(order_id, now(1), 20, Money.of("100.00"), None),
    ):
        with pytest.raises(BrokerRejectedError, match="FILLED"):
            action()


def test_an_amendment_must_leave_quantity_above_what_is_already_filled_and_keep_the_trigger() -> (
    None
):
    ex = exchange(ParticipationLiquidity(Decimal(1)))
    plain = ex.submit(request("A", qty=10), NOW).order.broker_order_id
    ex.on_tick(at("100.00", volume=1000), NOW)
    ex.on_tick(at("100.00", volume=1004, seconds=1), now(1))  # 4 filled
    with pytest.raises(BrokerRejectedError, match="already filled"):
        ex.amend(plain, now(2), 4, Money.of("100.00"), None)
    with pytest.raises(BrokerRejectedError, match="trigger"):
        ex.amend(plain, now(2), 8, Money.of("100.00"), Money.of("101.00"))


def test_a_rejected_order_is_recorded_with_its_reason_and_never_trades() -> None:
    ex = exchange()

    event = ex.submit(request("A"), NOW, reject_reason="insufficient funds")

    assert event.order.status is S.REJECTED and event.order.status_message == "insufficient funds"
    assert ex.on_tick(at("90.00"), NOW) == [] and ex.working_orders() == []
    assert [o.client_tag for o in ex.find_by_tag("A")] == ["A"]


def test_unknown_orders_are_refused_definitively() -> None:
    with pytest.raises(BrokerRejectedError, match="unknown order"):
        exchange().cancel("nope", NOW)


def test_restore_puts_orders_back_and_continues_their_sequences_and_trade_numbers() -> None:
    ex = exchange(ParticipationLiquidity(Decimal(1)))
    live = BrokerOrder(
        "PAPER1", "TAG", ID, OrderSide.BUY, OrderType.LIMIT, 10, 4, S.PARTIALLY_FILLED,
        Money.of("100.00"), average_price=Money.of("100.00"), updated_at=NOW,
    )  # fmt: skip
    ex.restore([RestoredOrder(live, seq=2, trades=1)], NOW)
    ex.on_tick(at("100.00", volume=1000), NOW)

    event = ex.on_tick(at("100.00", volume=1006, seconds=1), now(1))[0]

    assert (event.seq, event.order.filled_quantity) == (3, 10)
    assert event.trade is not None and event.trade.trade_id == "PAPER1-2"


def test_restore_is_only_for_an_empty_exchange() -> None:
    ex = exchange()
    ex.submit(request("A"), NOW)
    with pytest.raises(ValueError, match="empty"):
        ex.restore([], NOW)


def test_the_event_type_is_a_plain_value() -> None:
    event = exchange().submit(request("A"), NOW)
    assert isinstance(event, ExchangeEvent) and event.reason == ""
