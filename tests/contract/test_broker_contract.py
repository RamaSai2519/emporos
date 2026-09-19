"""EM-62: the shared `Broker` contract. Every implementation must pass EVERY test here — that is
what guarantees a strategy behaves identically in backtest, paper and live (plan.md §5).

The tests use only the `Broker` ABC and the harness hooks; nothing here knows Angel One."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from emporos.broker.errors import BrokerError, BrokerRejectedError
from emporos.broker.models import (
    BrokerOrderStatus,
    BrokerOrderUpdate,
    CancelOrderRequest,
    CandleRequest,
    MarketDataMode,
    ModifyOrderRequest,
    PlaceOrderRequest,
)
from emporos.core.errors import ErrorClassification
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from tests.contract.harnesses import HARNESS_FACTORIES, BrokerHarness
from tests.support.fakes import make_tick

pytestmark = pytest.mark.contract

T0 = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)  # 09:15 IST
ID = "NSE:3045"


@pytest.fixture(params=sorted(HARNESS_FACTORIES))
def h(request: pytest.FixtureRequest) -> Iterator[BrokerHarness]:
    yield HARNESS_FACTORIES[request.param]()


def limit(
    tag: str = "TAG001", qty: int = 10, price: str = "996.20", **kw: object
) -> PlaceOrderRequest:
    return PlaceOrderRequest(ID, OrderSide.BUY, OrderType.LIMIT, qty, Money.of(price), tag, **kw)  # type: ignore[arg-type]


# ---- session ------------------------------------------------------------------------------


async def test_a_session_has_a_forward_lifetime_and_ensure_is_idempotent(h: BrokerHarness) -> None:
    fresh = await h.broker.authenticate()
    again = await h.broker.ensure_session()

    assert fresh.client_code and fresh.expires_at > fresh.established_at
    assert again == fresh  # ensure_session did not log in a second time


async def test_ensure_session_logs_in_again_after_a_logout(h: BrokerHarness) -> None:
    first = await h.broker.ensure_session()
    await h.broker.logout()
    second = await h.broker.ensure_session()
    assert second.client_code == first.client_code and second.expires_at > second.established_at


async def test_the_profile_names_the_client_and_its_cash_exchanges(h: BrokerHarness) -> None:
    profile = await h.broker.get_profile()
    assert profile.client_code and profile.exchanges


# ---- reference and market data ------------------------------------------------------------


async def test_the_instrument_list_contains_the_watchlist_instrument(h: BrokerHarness) -> None:
    assert h.instrument.instrument_id in {i.instrument_id for i in await h.broker.get_instruments()}


async def test_quotes_carry_exact_money_and_keep_the_callers_order(h: BrokerHarness) -> None:
    quotes = await h.broker.get_quote(["NSE:2885", ID])

    assert [q.instrument_id for q in quotes] == ["NSE:2885", ID]
    assert all(isinstance(q.ltp, Money) and q.ltp > Money.zero() for q in quotes)


async def test_an_unknown_instrument_is_refused_definitively(h: BrokerHarness) -> None:
    with pytest.raises(BrokerRejectedError) as raised:
        await h.broker.get_quote(["NSE:999999"])
    assert raised.value.classification is ErrorClassification.DEFINITIVE
    with pytest.raises(BrokerRejectedError):
        await h.broker.subscribe_market_data(["NSE:999999"], MarketDataMode.QUOTE)


async def test_history_is_ordered_unique_and_bounded_by_the_request(h: BrokerHarness) -> None:
    def bar(minute: int) -> Candle:
        p = Money.of(f"{100 + minute / 100:.2f}")
        return Candle(ID, Timeframe.M1, T0 + timedelta(minutes=minute), p, p, p, p, 10)

    h.history.extend([bar(3), bar(1), bar(2), bar(2), bar(9)])

    bars = await h.broker.get_historical_candles(
        CandleRequest(ID, Timeframe.M1, T0 + timedelta(minutes=1), T0 + timedelta(minutes=4))
    )

    assert [b.ts for b in bars] == [T0 + timedelta(minutes=m) for m in (1, 2, 3)]  # bounded, unique
    assert all(isinstance(b.close, Money) for b in bars)


async def test_ticks_reach_every_handler_in_order_and_a_failing_handler_starves_nobody(
    h: BrokerHarness,
) -> None:
    seen: list[str] = []

    def broken(tick: object) -> None:
        raise RuntimeError("handler bug")

    h.broker.on_tick(lambda t: seen.append(f"first:{t.sequence}"))
    h.broker.on_tick(broken)
    h.broker.on_tick(lambda t: seen.append(f"last:{t.sequence}"))
    await h.broker.subscribe_market_data([ID], MarketDataMode.LTP)

    h.emit_tick(make_tick(T0, instrument_id=ID, sequence=1))
    h.emit_tick(make_tick(T0, instrument_id=ID, sequence=2))

    assert seen == ["first:1", "last:1", "first:2", "last:2"]


# ---- orders: the idempotency contract (Decision 7) ------------------------------------------


async def test_a_placed_order_acks_with_the_clients_tag_and_appears_in_the_book(
    h: BrokerHarness,
) -> None:
    ack = await h.broker.place_order(limit("TAG001", qty=10, price="996.20"))
    book = await h.broker.get_order_book()

    assert ack.broker_order_id and ack.client_tag == "TAG001"
    (order,) = (o for o in book if o.broker_order_id == ack.broker_order_id)
    assert (order.client_tag, order.instrument_id, order.side) == ("TAG001", ID, OrderSide.BUY)
    assert (order.order_type, order.quantity, order.filled_quantity) == (OrderType.LIMIT, 10, 0)
    assert order.price == Money.of("996.20")  # exact, never a float
    assert order.status in {BrokerOrderStatus.PENDING, BrokerOrderStatus.OPEN}


async def test_a_stoploss_limit_order_keeps_its_trigger(h: BrokerHarness) -> None:
    request = PlaceOrderRequest(
        ID,
        OrderSide.SELL,
        OrderType.STOPLOSS_LIMIT,
        5,
        Money.of("990"),
        "SL0001",
        trigger_price=Money.of("991"),
    )
    ack = await h.broker.place_order(request)

    (order,) = await h.broker.find_orders_by_tag("SL0001")

    assert (
        order.broker_order_id == ack.broker_order_id
        and order.order_type is OrderType.STOPLOSS_LIMIT
    )
    assert (
        order.trigger_price == Money.of("991") and order.status is BrokerOrderStatus.TRIGGER_PENDING
    )


async def test_find_orders_by_tag_matches_exactly_and_never_by_prefix_or_case(
    h: BrokerHarness,
) -> None:
    for tag in ("ABC123", "ABC1234", "abc123"):
        await h.broker.place_order(limit(tag))

    found = await h.broker.find_orders_by_tag("ABC123")

    assert [o.client_tag for o in found] == ["ABC123"]
    assert await h.broker.find_orders_by_tag("NOSUCH1") == []


async def test_an_empty_tag_is_refused_because_it_could_claim_untagged_orders(
    h: BrokerHarness,
) -> None:
    with pytest.raises(ValueError, match="tag"):
        await h.broker.find_orders_by_tag("")


async def test_placing_once_creates_exactly_one_order(h: BrokerHarness) -> None:
    await h.broker.place_order(limit("ONCE01"))
    assert len(await h.broker.find_orders_by_tag("ONCE01")) == 1


async def test_a_lost_reply_is_ambiguous_and_the_tag_reveals_the_order_that_exists(
    h: BrokerHarness,
) -> None:
    """The heart of Decision 7: the outcome is unknown, the order is real, and only a lookup by
    tag — never a resend — resolves it."""
    h.lose_next_place_reply()

    with pytest.raises(BrokerError) as raised:
        await h.broker.place_order(limit("LOST01"))
    assert raised.value.classification is ErrorClassification.AMBIGUOUS

    found = await h.broker.find_orders_by_tag("LOST01")

    assert len(found) == 1  # the order exists at the broker...
    assert len(await h.broker.get_order_book()) == 1  # ...and nothing was resent behind our back


async def test_an_order_for_an_unknown_instrument_is_refused_and_creates_nothing(
    h: BrokerHarness,
) -> None:
    bad = PlaceOrderRequest(
        "NSE:999999", OrderSide.BUY, OrderType.LIMIT, 1, Money.of("1"), "BAD001"
    )
    with pytest.raises(BrokerRejectedError):
        await h.broker.place_order(bad)
    assert await h.broker.get_order_book() == []


# ---- order lifecycle ------------------------------------------------------------------------


async def test_cancelling_makes_the_order_terminal(h: BrokerHarness) -> None:
    ack = await h.broker.place_order(limit("CXL001"))

    await h.broker.cancel_order(CancelOrderRequest(ack.broker_order_id, OrderType.LIMIT))

    (order,) = await h.broker.find_orders_by_tag("CXL001")
    assert order.status is BrokerOrderStatus.CANCELLED and order.status.is_terminal


async def test_modifying_changes_quantity_and_price(h: BrokerHarness) -> None:
    ack = await h.broker.place_order(limit("MOD001", qty=10, price="996.20"))

    await h.broker.modify_order(
        ModifyOrderRequest(ack.broker_order_id, ID, OrderType.LIMIT, 4, Money.of("995.10"))
    )

    (order,) = await h.broker.find_orders_by_tag("MOD001")
    assert (order.quantity, order.price) == (4, Money.of("995.10"))


async def test_fills_move_the_order_through_partial_to_filled_and_reach_the_books(
    h: BrokerHarness,
) -> None:
    updates: list[BrokerOrderUpdate] = []
    h.broker.on_order_update(updates.append)
    ack = await h.broker.place_order(limit("FIL001", qty=10, price="996.20"))

    h.fill(ack.broker_order_id, 4, Money.of("996.20"))
    (partial,) = await h.broker.find_orders_by_tag("FIL001")
    h.fill(ack.broker_order_id, 6, Money.of("996.10"))
    (done,) = await h.broker.find_orders_by_tag("FIL001")

    assert (partial.status, partial.filled_quantity) == (BrokerOrderStatus.PARTIALLY_FILLED, 4)
    assert (done.status, done.filled_quantity) == (BrokerOrderStatus.FILLED, 10)
    trades = await h.broker.get_trade_book()
    assert [(t.quantity, t.price) for t in trades] == [
        (4, Money.of("996.20")),
        (6, Money.of("996.10")),
    ]
    assert all(t.broker_order_id == ack.broker_order_id and t.side is OrderSide.BUY for t in trades)
    assert [u.order.filled_quantity for u in updates][-2:] == [4, 10]  # pushed updates track state
    assert all(u.order.client_tag == "FIL001" for u in updates[-2:])  # and carry the tag


async def test_positions_reflect_fills(h: BrokerHarness) -> None:
    assert await h.broker.get_positions() == []  # an empty account lists as empty
    ack = await h.broker.place_order(limit("POS001", qty=10))

    h.fill(ack.broker_order_id, 10, Money.of("996.20"))

    (position,) = await h.broker.get_positions()
    assert (position.instrument_id, position.net_quantity) == (ID, 10)


# ---- account ---------------------------------------------------------------------------------


async def test_funds_are_exact_money_and_an_empty_account_lists_empty_books(
    h: BrokerHarness,
) -> None:
    funds = await h.broker.get_funds()

    assert isinstance(funds.net, Money) and isinstance(funds.available_cash, Money)
    assert await h.broker.get_order_book() == []
    assert await h.broker.get_trade_book() == []
    assert await h.broker.get_holdings() == []


# ---- values are immutable --------------------------------------------------------------------


async def test_returned_objects_are_immutable_values(h: BrokerHarness) -> None:
    await h.broker.place_order(limit("IMM001"))
    (order,) = await h.broker.find_orders_by_tag("IMM001")
    session = await h.broker.ensure_session()

    with pytest.raises(dataclasses.FrozenInstanceError):
        order.status = BrokerOrderStatus.FILLED  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        session.client_code = "someone else"  # type: ignore[misc]


def test_the_suite_runs_against_more_than_one_implementation() -> None:
    assert len(HARNESS_FACTORIES) >= 2  # a contract with one implementation proves nothing
