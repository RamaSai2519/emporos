"""EM-61: domain <-> SmartAPI translation, in both directions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from emporos.broker.angelone.mapping import (
    AccountMapper,
    InstrumentLocator,
    OrderRequestMapper,
    OrderStatusMapper,
    instrument_id_of,
    order_from_update_data,
    parse_exchange_time,
)
from emporos.broker.angelone.models import (
    FundsResponse,
    HoldingEntry,
    OrderBookEntry,
    PositionEntry,
    ProfileResponse,
    QuoteEntry,
    TradeBookEntry,
)
from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import (
    BrokerOrderStatus,
    CancelOrderRequest,
    ModifyOrderRequest,
    PlaceOrderRequest,
    ProductType,
)
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.instruments.cache import InstrumentCache
from tests.support.fakes import make_instrument

SBIN = make_instrument("3045", symbol="SBIN-EQ")
LOCATOR = InstrumentLocator(InstrumentCache([SBIN]))
REQUESTS = OrderRequestMapper(LOCATOR)
ACCOUNT = AccountMapper()


def place(**overrides: Any) -> PlaceOrderRequest:
    fields: dict[str, Any] = {
        "instrument_id": "NSE:3045",
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": 10,
        "price": Money.of("996.20"),
        "client_tag": "ABC123",
    }
    return PlaceOrderRequest(**{**fields, **overrides})


def test_a_limit_order_becomes_the_documented_request_body() -> None:
    assert REQUESTS.place(place()) == {
        "variety": "NORMAL",
        "tradingsymbol": "SBIN-EQ",
        "symboltoken": "3045",
        "transactiontype": "BUY",
        "exchange": "NSE",
        "ordertype": "LIMIT",
        "producttype": "INTRADAY",
        "duration": "DAY",
        "price": "996.20",
        "quantity": "10",
        "ordertag": "ABC123",
    }


def test_a_stoploss_limit_order_carries_its_trigger_and_variety() -> None:
    body = REQUESTS.place(
        place(
            order_type=OrderType.STOPLOSS_LIMIT, trigger_price=Money.of("990"), side=OrderSide.SELL
        )
    )
    assert (body["variety"], body["ordertype"], body["triggerprice"]) == (
        "STOPLOSS", "STOPLOSS_LIMIT", "990",
    )  # fmt: skip
    assert body["transactiontype"] == "SELL"


def test_no_order_this_mapper_can_emit_is_a_market_or_ioc_order() -> None:
    for order_type in OrderType:
        extra = {"trigger_price": Money.of("990")} if order_type is OrderType.STOPLOSS_LIMIT else {}
        body = REQUESTS.place(place(order_type=order_type, **extra))
        assert body["ordertype"] in {"LIMIT", "STOPLOSS_LIMIT"}
        assert body["duration"] == "DAY"  # never IOC


def test_prices_are_sent_as_exact_decimal_text_never_a_float() -> None:
    assert REQUESTS.place(place(price=Money.of("0.05")))["price"] == "0.05"
    assert REQUESTS.place(place(price=Money.of("1234567.85")))["price"] == "1234567.85"
    assert REQUESTS.place(place(product=ProductType.DELIVERY))["producttype"] == "DELIVERY"


def test_modify_and_cancel_bodies() -> None:
    modify = REQUESTS.modify(
        ModifyOrderRequest("201", "NSE:3045", OrderType.LIMIT, 7, Money.of("997"))
    )
    assert modify["orderid"] == "201" and modify["quantity"] == "7" and modify["price"] == "997"
    assert (modify["tradingsymbol"], modify["symboltoken"], modify["exchange"]) == (
        "SBIN-EQ",
        "3045",
        "NSE",
    )
    assert REQUESTS.cancel(CancelOrderRequest("201", OrderType.STOPLOSS_LIMIT)) == {
        "variety": "STOPLOSS",
        "orderid": "201",
    }


def test_an_unknown_instrument_is_a_definitive_rejection_before_anything_is_sent() -> None:
    with pytest.raises(BrokerRejectedError, match="unknown instrument"):
        REQUESTS.place(place(instrument_id="NSE:999999"))


@pytest.mark.parametrize(
    ("text", "filled", "quantity", "expected"),
    [
        ("open", 0, 10, BrokerOrderStatus.OPEN),
        ("open", 4, 10, BrokerOrderStatus.PARTIALLY_FILLED),
        ("open", 10, 10, BrokerOrderStatus.OPEN),
        ("OPEN", 0, 10, BrokerOrderStatus.OPEN),
        ("complete", 10, 10, BrokerOrderStatus.FILLED),
        ("cancelled", 3, 10, BrokerOrderStatus.CANCELLED),
        ("rejected", 0, 10, BrokerOrderStatus.REJECTED),
        ("trigger pending", 0, 10, BrokerOrderStatus.TRIGGER_PENDING),
        ("validation pending", 0, 10, BrokerOrderStatus.PENDING),
        ("put order req received", 0, 10, BrokerOrderStatus.PENDING),
        ("modified", 0, 10, BrokerOrderStatus.OPEN),
        ("something new", 0, 10, BrokerOrderStatus.UNRECOGNISED),
        ("", 0, 10, BrokerOrderStatus.UNRECOGNISED),
        (None, 0, 10, BrokerOrderStatus.UNRECOGNISED),
    ],
)
def test_order_status_mapping(
    text: str | None, filled: int, quantity: int, expected: BrokerOrderStatus
) -> None:
    assert OrderStatusMapper().map(text, filled, quantity) is expected


def book_entry(**overrides: Any) -> OrderBookEntry:
    row: dict[str, Any] = {
        "orderid": "201", "exchange": "NSE", "symboltoken": "3045", "transactiontype": "BUY",
        "ordertype": "LIMIT", "quantity": "10", "filledshares": "0", "price": "996.2",
        "triggerprice": "0", "averageprice": "0", "ordertag": "ABC123", "orderstatus": "open",
        "text": "", "updatetime": "18-Sep-2026 10:00:05",
    }  # fmt: skip
    return OrderBookEntry.model_validate({**row, **overrides})


def test_an_order_book_row_maps_to_a_neutral_order() -> None:
    order = ACCOUNT.order(book_entry())

    assert (order.broker_order_id, order.client_tag, order.instrument_id) == (
        "201",
        "ABC123",
        "NSE:3045",
    )
    assert (order.side, order.order_type, order.quantity, order.filled_quantity) == (
        OrderSide.BUY, OrderType.LIMIT, 10, 0,
    )  # fmt: skip
    assert order.status is BrokerOrderStatus.OPEN and order.price == Money.of("996.2")
    assert order.updated_at == datetime(2026, 9, 18, 4, 30, 5, tzinfo=UTC)  # 10:00:05 IST


def test_an_order_placed_in_the_brokers_app_lists_without_pretending_to_be_ours() -> None:
    app_order = ACCOUNT.order(book_entry(ordertype="MARKET", ordertag=""))
    assert app_order.order_type is None and app_order.client_tag is None  # blank tag != our tag


def test_partial_fills_are_recognised_from_the_filled_quantity() -> None:
    order = ACCOUNT.order(book_entry(filledshares="4"))
    assert order.status is BrokerOrderStatus.PARTIALLY_FILLED and order.filled_quantity == 4


def test_an_unreadable_timestamp_never_breaks_a_listing() -> None:
    assert ACCOUNT.order(book_entry(updatetime="garbage")).updated_at is None
    assert parse_exchange_time(None) is None and parse_exchange_time("") is None


def test_a_row_with_an_unknown_side_is_rejected_not_guessed() -> None:
    with pytest.raises(BrokerRejectedError):
        ACCOUNT.order(book_entry(transactiontype="SHORT"))


def test_the_instrument_id_is_derivable_from_the_row_alone() -> None:
    assert instrument_id_of("nse", "3045") == "NSE:3045"
    assert (
        ACCOUNT.order(book_entry(exchange="NFO", symboltoken="55555")).instrument_id == "NFO:55555"
    )


def test_trade_position_holding_funds_profile_and_quote_mappings() -> None:
    trade = ACCOUNT.trade(
        TradeBookEntry.model_validate(
            {
                "orderid": "201",
                "fillid": "7001",
                "exchange": "NSE",
                "symboltoken": "3045",
                "transactiontype": "SELL",
                "fillprice": "995",
                "fillsize": "5",
                "filltime": "10:05:00",
            }
        )  # fmt: skip
    )
    assert (trade.trade_id, trade.quantity, trade.price, trade.side) == (
        "7001",
        5,
        Money.of("995"),
        OrderSide.SELL,
    )

    position = ACCOUNT.position(
        PositionEntry.model_validate(
            {
                "exchange": "NSE",
                "symboltoken": "3045",
                "netqty": "-5",
                "avgnetprice": "",
                "netprice": "996.20",
                "ltp": "997.1",
                "realised": "12.5",
                "unrealised": "",
            }
        )  # fmt: skip
    )
    assert position.net_quantity == -5 and position.average_price == Money.of(
        "996.20"
    )  # falls back
    assert position.realized_pnl == Money.of("12.5") and position.unrealized_pnl is None

    holding = ACCOUNT.holding(
        HoldingEntry.model_validate(
            {"exchange": "NSE", "symboltoken": "3045", "quantity": 10, "averageprice": 950.5}
        )
    )
    assert holding.average_price == Money.of("950.5") and holding.quantity == 10

    funds = ACCOUNT.funds(
        FundsResponse.model_validate({"net": "100000.00", "availablecash": "99000.5"})
    )
    assert (funds.net, funds.available_cash, funds.utilised) == (
        Money.of("100000.00"),
        Money.of("99000.5"),
        None,
    )

    profile = ACCOUNT.profile(
        ProfileResponse.model_validate(
            {
                "clientcode": "A1",
                "exchanges": ["nse_fo", "nse_cm", "bse_cm", "mcx_fo"],
                "products": [],
            }
        )
    )
    assert profile.exchanges == (Exchange.NSE, Exchange.BSE)  # cash segments only


def test_an_order_update_payload_maps_to_the_order_it_describes() -> None:
    data = {
        "orderid": "201", "exchange": "NSE", "symboltoken": "3045", "transactiontype": "BUY",
        "quantity": "10", "ordertype": "LIMIT", "ordertag": "ABC123", "orderstatus": "complete",
        "filledshares": "10", "price": "996.2", "averageprice": "996.2", "unrelated": "ignored",
    }  # fmt: skip
    order = order_from_update_data(data, ACCOUNT)
    assert order.client_tag == "ABC123" and order.status is BrokerOrderStatus.FILLED
    assert order.average_price == Money.of("996.2") and isinstance(
        order.average_price.amount, Decimal
    )  # type: ignore[union-attr]


@given(
    tag=st.text(max_size=12),
    status=st.text(max_size=20),
    order_type=st.text(max_size=12),
    when=st.text(max_size=25),
    filled=st.one_of(st.integers(0, 50).map(str), st.just("")),
)
def test_no_optional_field_can_make_an_order_book_row_unmappable(
    tag: str, status: str, order_type: str, when: str, filled: str
) -> None:
    entry = book_entry(
        ordertag=tag, orderstatus=status, ordertype=order_type, updatetime=when, filledshares=filled
    )
    order = ACCOUNT.order(entry)
    assert order.broker_order_id == "201"


class TestQuoteDepth:
    """Best bid/ask come from the top non-empty level; SmartAPI pads empty levels with zeros."""

    def _entry(self, buy: list[dict], sell: list[dict] | None) -> QuoteEntry:  # type: ignore[type-arg]
        body = {
            "exchange": "NSE", "tradingSymbol": "SBIN-EQ", "symbolToken": "3045",
            "ltp": "996.2", "open": "990", "high": "1000", "low": "985", "close": "988.7",
            "tradeVolume": 1000, "lowerCircuit": "890", "upperCircuit": "1087",
            "exchTradeTime": "18-Sep-2026 15:30:00",
        }  # fmt: skip
        if sell is not None:
            body["depth"] = {"buy": buy, "sell": sell}
        return QuoteEntry.model_validate(body)

    def test_the_best_bid_and_ask_are_the_first_non_empty_levels(self) -> None:
        empty = {"price": "0", "quantity": 0}
        quote = AccountMapper.quote(
            self._entry(
                [empty, {"price": "996.1", "quantity": 50}, {"price": "996.0", "quantity": 9}],
                [{"price": "996.2", "quantity": 408}, empty],
            )
        )
        assert (quote.bid, quote.ask) == (Money.of("996.1"), Money.of("996.2"))

    def test_an_empty_side_or_missing_depth_is_none_not_zero(self) -> None:
        empty = {"price": "0", "quantity": 0}
        one_sided = AccountMapper.quote(self._entry([empty], [{"price": "996.2", "quantity": 1}]))
        assert one_sided.bid is None and one_sided.ask == Money.of("996.2")
        no_depth = AccountMapper.quote(self._entry([], None))
        assert (no_depth.bid, no_depth.ask) == (None, None)
