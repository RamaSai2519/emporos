"""EM-61: every `Broker` method is implemented and delegates correctly (Angel One adapter)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from emporos.broker.errors import BrokerRejectedError, BrokerTransportError
from emporos.broker.models import (
    BrokerOrderStatus,
    CancelOrderRequest,
    CandleRequest,
    MarketDataMode,
    ModifyOrderRequest,
    PlaceOrderRequest,
)
from emporos.core.errors import ErrorClassification
from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from tests.support.angelone_broker import NOW, RELIANCE, SBIN, BrokerRig
from tests.support.candles import make_candle
from tests.support.fakes import ScriptedRestTransport, make_tick


def order_row(tag: str | None, order_id: str = "201", **overrides: Any) -> dict[str, Any]:
    row = {
        "orderid": order_id, "exchange": "NSE", "symboltoken": "3045", "transactiontype": "BUY",
        "ordertype": "LIMIT", "quantity": "10", "filledshares": "0", "price": "996.2",
        "orderstatus": "open", "ordertag": tag or "", "updatetime": "18-Sep-2026 10:00:05",
    }  # fmt: skip
    return row | overrides


def place() -> PlaceOrderRequest:
    return PlaceOrderRequest(
        "NSE:3045", OrderSide.BUY, OrderType.LIMIT, 10, Money.of("996.20"), "ABC123"
    )


# ---- session ----------------------------------------------------------------------------


async def test_session_methods_delegate_and_expose_no_secrets() -> None:
    rig = BrokerRig()

    fresh = await rig.broker.authenticate()
    again = await rig.broker.ensure_session()
    await rig.broker.logout()

    assert rig.sessions.logins == 1 and rig.sessions.logouts == 1
    assert (fresh.client_code, fresh.established_at) == ("A0000000", NOW)
    assert again == fresh  # ensure_session reuses the live session
    assert "jwt" not in repr(fresh).lower()


async def test_profile_and_funds_map_to_neutral_types() -> None:
    rig = BrokerRig(
        ScriptedRestTransport(
            getProfile=[{"clientcode": "A1", "exchanges": ["nse_cm", "bse_cm"], "products": []}],
            getRMS=[{"net": "100000.00", "availablecash": "99000.50"}],
        )
    )

    profile, funds = await rig.broker.get_profile(), await rig.broker.get_funds()

    assert profile.client_code == "A1" and len(profile.exchanges) == 2
    assert funds.available_cash == Money.of("99000.50") and isinstance(
        funds.net.amount, type(Money.of(1).amount)
    )


async def test_instruments_come_from_the_catalog() -> None:
    assert list(await BrokerRig().broker.get_instruments()) == [SBIN, RELIANCE]


# ---- market data ------------------------------------------------------------------------


async def test_quotes_are_batched_ordered_by_request_and_omit_unknown_symbols() -> None:
    entry = lambda token, sym: {  # noqa: E731
        "exchange": "NSE", "tradingSymbol": sym, "symbolToken": token, "ltp": 1.5, "open": 1,
        "high": 2, "low": 1, "close": 1, "tradeVolume": 9, "lowerCircuit": 1, "upperCircuit": 2,
        "exchTradeTime": "18-Sep-2026 15:59:57",
    }  # fmt: skip
    rig = BrokerRig(
        ScriptedRestTransport(
            quote=[
                {
                    "fetched": [entry("2885", "RELIANCE-EQ"), entry("3045", "SBIN-EQ")],
                    "unfetched": [],
                }
            ]
        )
    )

    quotes = await rig.broker.get_quote(["NSE:3045", "NSE:2885"])

    assert [q.instrument_id for q in quotes] == ["NSE:3045", "NSE:2885"]  # the caller's order
    (request,) = rig.transport.sent_to("quote")
    assert request.body["exchangeTokens"] == {"NSE": ["3045", "2885"]}
    assert quotes[0].ltp == Money.of("1.5") and quotes[0].exchange_ts is not None


async def test_a_quote_request_larger_than_the_broker_batch_is_split() -> None:
    from emporos.instruments.cache import InstrumentCache
    from tests.support.fakes import make_instrument

    many = [make_instrument(str(1000 + n)) for n in range(120)]
    rig = BrokerRig(ScriptedRestTransport(quote=[{"fetched": [], "unfetched": []}] * 3))
    rig.broker._locator._resolver = InstrumentCache(many)  # type: ignore[attr-defined]

    assert await rig.broker.get_quote([i.instrument_id for i in many]) == []

    sizes = [len(r.body["exchangeTokens"]["NSE"]) for r in rig.transport.sent_to("quote")]
    assert sizes == [50, 50, 20]


async def test_historical_candles_filter_to_the_requested_range_and_are_sorted_unique() -> None:
    inside = [make_candle("NSE:3045", NOW + timedelta(minutes=m)) for m in (2, 0, 1)]
    outside = make_candle("NSE:3045", NOW + timedelta(hours=5))
    rig = BrokerRig()
    rig.candles._bars = lambda *_: [*inside, inside[0], outside]

    bars = await rig.broker.get_historical_candles(
        CandleRequest("NSE:3045", Timeframe.M1, NOW, NOW + timedelta(hours=1))
    )

    assert [b.ts for b in bars] == [NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)]


async def test_a_long_one_minute_request_is_split_below_the_brokers_truncation_limit() -> None:
    rig = BrokerRig()

    await rig.broker.get_historical_candles(
        CandleRequest("NSE:3045", Timeframe.M1, NOW - timedelta(days=100), NOW)
    )

    spans = [end - start for _, _, start, end in rig.candles.calls]
    assert len(spans) == 4 and all(s <= timedelta(days=30) for s in spans)
    starts = [start for _, _, start, _ in rig.candles.calls]
    assert (
        starts[0] == NOW - timedelta(days=100) and rig.candles.calls[-1][3] == NOW
    )  # contiguous, complete


async def test_other_timeframes_are_requested_in_one_call() -> None:
    rig = BrokerRig()
    await rig.broker.get_historical_candles(
        CandleRequest("NSE:3045", Timeframe.D1, NOW - timedelta(days=300), NOW)
    )
    assert len(rig.candles.calls) == 1


async def test_market_data_subscription_resolves_instruments_and_ignores_mode_as_a_minimum() -> (
    None
):
    rig = BrokerRig()

    await rig.broker.subscribe_market_data(["NSE:3045", "NSE:2885"], MarketDataMode.LTP)
    await rig.broker.unsubscribe_market_data(["NSE:2885"])

    assert rig.market_data.subscribed == [["NSE:3045", "NSE:2885"]]
    assert rig.market_data.unsubscribed == [["NSE:2885"]]


async def test_registered_tick_handlers_receive_ticks() -> None:
    rig = BrokerRig()
    seen: list[str] = []
    rig.broker.on_tick(lambda tick: seen.append(tick.instrument_id))

    rig.ticks.emit(make_tick(NOW, instrument_id="NSE:3045"))

    assert seen == ["NSE:3045"]


# ---- orders -----------------------------------------------------------------------------


async def test_place_order_sends_the_mapped_body_and_acks_with_the_clients_tag() -> None:
    rig = BrokerRig(ScriptedRestTransport(placeOrder=[{"script": "SBIN-EQ", "orderid": "201"}]))

    ack = await rig.broker.place_order(place())

    (sent,) = rig.transport.sent_to("placeOrder")
    assert sent.body["ordertag"] == "ABC123" and sent.body["ordertype"] == "LIMIT"
    assert (ack.broker_order_id, ack.client_tag) == ("201", "ABC123")


async def test_modify_and_cancel_ack_with_the_order_id() -> None:
    rig = BrokerRig(
        ScriptedRestTransport(modifyOrder=[{"orderid": "201"}], cancelOrder=[{"orderid": "201"}])
    )

    modified = await rig.broker.modify_order(
        ModifyOrderRequest("201", "NSE:3045", OrderType.LIMIT, 5, Money.of("997"))
    )
    cancelled = await rig.broker.cancel_order(CancelOrderRequest("201", OrderType.LIMIT))

    assert modified.broker_order_id == cancelled.broker_order_id == "201"
    assert rig.transport.sent_to("cancelOrder")[0].body == {"variety": "NORMAL", "orderid": "201"}


async def test_an_unknown_instrument_is_refused_and_nothing_reaches_the_broker() -> None:
    rig = BrokerRig()
    bad = PlaceOrderRequest("NSE:999999", OrderSide.BUY, OrderType.LIMIT, 1, Money.of("1"), "T1")

    with pytest.raises(BrokerRejectedError):
        await rig.broker.place_order(bad)

    assert rig.transport.requests == []


async def test_an_ambiguous_placement_is_resolved_by_tag_never_by_resending() -> None:
    """Decision 7 end to end: the reply is lost, the order exists; only a tag lookup finds it."""
    rig = BrokerRig(
        ScriptedRestTransport(
            placeOrder=[BrokerTransportError("timeout")],
            getOrderBook=[[order_row("OTHER1", "199"), order_row("ABC123", "201")]],
        )
    )

    with pytest.raises(BrokerTransportError) as raised:
        await rig.broker.place_order(place())
    assert raised.value.classification is ErrorClassification.AMBIGUOUS

    found = await rig.broker.find_orders_by_tag("ABC123")

    assert [o.broker_order_id for o in found] == ["201"]
    assert len(rig.transport.sent_to("placeOrder")) == 1  # it was never resent


async def test_find_orders_by_tag_is_exact_and_never_matches_untagged_orders() -> None:
    rig = BrokerRig(
        ScriptedRestTransport(
            getOrderBook=[
                [
                    order_row("ABC123", "1"),
                    order_row("ABC1234", "2"),  # a longer tag that merely starts the same
                    order_row("abc123", "3"),  # different case
                    order_row(None, "4"),  # entered in the broker's app: no tag
                    order_row("ABC123", "5", ordertype="MARKET"),  # same tag, foreign type
                ]
            ]
        )
    )

    found = await rig.broker.find_orders_by_tag("ABC123")

    assert [o.broker_order_id for o in found] == ["1", "5"]


async def test_an_empty_tag_is_refused_because_it_would_claim_untagged_orders() -> None:
    rig = BrokerRig()
    with pytest.raises(ValueError, match="tag"):
        await rig.broker.find_orders_by_tag("")
    assert rig.transport.requests == []


async def test_an_empty_account_lists_as_empty_not_as_an_error() -> None:
    """Recorded live: order book, trade book and positions answer `data: null`."""
    rig = BrokerRig(
        ScriptedRestTransport(
            getOrderBook=[None], getTradeBook=[None], getPosition=[None], getHolding=[[]]
        )
    )

    assert await rig.broker.get_order_book() == []
    assert await rig.broker.get_trade_book() == []
    assert await rig.broker.get_positions() == []
    assert await rig.broker.get_holdings() == []


async def test_an_unreadable_row_is_skipped_without_hiding_the_rest_of_the_book() -> None:
    rig = BrokerRig(
        ScriptedRestTransport(
            getOrderBook=[[order_row("BAD1", "1", transactiontype="SHORT"), order_row("OK1", "2")]],
            getTradeBook=[
                [
                    {
                        "orderid": "1",
                        "fillid": "9",
                        "exchange": "NSE",
                        "symboltoken": "3045",
                        "transactiontype": "SHORT",
                        "fillprice": "1",
                        "fillsize": "1",
                    },
                    {
                        "orderid": "2",
                        "fillid": "8",
                        "exchange": "NSE",
                        "symboltoken": "3045",
                        "transactiontype": "BUY",
                        "fillprice": "1",
                        "fillsize": "1",
                    },
                ]
            ],
        )  # fmt: skip
    )

    assert [o.broker_order_id for o in await rig.broker.get_order_book()] == ["2"]
    assert [t.trade_id for t in await rig.broker.get_trade_book()] == ["8"]


async def test_statuses_flow_through_from_the_book() -> None:
    rig = BrokerRig(
        ScriptedRestTransport(
            getOrderBook=[[order_row("A", "1", orderstatus="complete", filledshares="10")]]
        )
    )
    (order,) = await rig.broker.get_order_book()
    assert order.status is BrokerOrderStatus.FILLED and order.filled_quantity == 10


async def test_registered_order_update_handlers_receive_updates() -> None:
    rig = BrokerRig()
    seen: list[Any] = []
    rig.broker.on_order_update(seen.append)
    marker = object()

    rig.updates.emit(marker)  # type: ignore[arg-type]

    assert seen == [marker]


# ---- account ----------------------------------------------------------------------------


async def test_positions_and_holdings_map_to_neutral_types() -> None:
    rig = BrokerRig(
        ScriptedRestTransport(
            getPosition=[
                [{"exchange": "NSE", "symboltoken": "3045", "netqty": "5", "avgnetprice": "996.2"}]
            ],
            getHolding=[
                [{"exchange": "NSE", "symboltoken": "3045", "quantity": 10, "averageprice": 950.5}]
            ],
        )
    )

    (position,) = await rig.broker.get_positions()
    (holding,) = await rig.broker.get_holdings()

    assert (position.instrument_id, position.net_quantity) == ("NSE:3045", 5)
    assert holding.average_price == Money.of("950.5")
    assert UTC and datetime
