"""EM-60: broker-neutral DTOs are correct by construction; a forbidden order is unrepresentable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from emporos.broker.models import (
    MAX_CLIENT_TAG_LENGTH,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerSession,
    BrokerTrade,
    CancelOrderRequest,
    CandleRequest,
    MarketDataMode,
    ModifyOrderRequest,
    PlaceOrderRequest,
    ProductType,
    Quote,
    Validity,
)
from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType

NOW = datetime(2026, 9, 21, 4, 30, tzinfo=UTC)
IST = timezone(timedelta(hours=5, minutes=30))


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


def test_a_valid_limit_order_defaults_to_intraday_day_validity() -> None:
    order = place()
    assert order.product is ProductType.INTRADAY and order.validity is Validity.DAY
    assert order.trigger_price is None


def test_a_stoploss_limit_order_needs_a_trigger_and_a_limit_order_must_not_have_one() -> None:
    stop = place(order_type=OrderType.STOPLOSS_LIMIT, trigger_price=Money.of("990"))
    assert stop.trigger_price == Money.of("990")
    with pytest.raises(ValueError, match="trigger"):
        place(order_type=OrderType.STOPLOSS_LIMIT)
    with pytest.raises(ValueError, match="trigger"):
        place(trigger_price=Money.of("990"))


def test_market_and_ioc_orders_cannot_be_expressed_at_all() -> None:
    assert {t.value for t in OrderType} == {"LIMIT", "STOPLOSS_LIMIT"}
    assert {v.value for v in Validity} == {"DAY"}
    for forbidden in ("MARKET", "SL-M", "IOC"):
        with pytest.raises(TypeError):
            place(order_type=forbidden)  # even a hand-built string cannot get through
        with pytest.raises(TypeError):
            place(validity=forbidden)
    with pytest.raises(TypeError):
        place(side="SHORT")
    with pytest.raises(TypeError):
        ModifyOrderRequest("1", "NSE:1", "MARKET", 1, Money.of("1"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        CancelOrderRequest("1", "MARKET")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity": 0},
        {"quantity": -1},
        {"price": Money.zero()},
        {"price": Money.of("-1")},
        {"client_tag": ""},
        {"client_tag": "has space"},
        {"client_tag": "a" * (MAX_CLIENT_TAG_LENGTH + 1)},
        {"client_tag": "tag-with-dash"},
    ],
)
def test_invalid_orders_are_rejected_at_construction(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="."):
        place(**overrides)


def test_the_longest_allowed_tag_is_accepted() -> None:
    assert place(client_tag="A" * MAX_CLIENT_TAG_LENGTH).client_tag == "A" * MAX_CLIENT_TAG_LENGTH


def test_modify_and_cancel_requests_are_validated() -> None:
    ModifyOrderRequest("1", "NSE:1", OrderType.LIMIT, 5, Money.of("10"))
    for bad in (
        ("", "NSE:1", OrderType.LIMIT, 5, Money.of("10")),
        ("1", "NSE:1", OrderType.LIMIT, 0, Money.of("10")),
        ("1", "NSE:1", OrderType.LIMIT, 5, Money.zero()),
    ):
        with pytest.raises(ValueError, match="."):
            ModifyOrderRequest(*bad)
    with pytest.raises(ValueError, match="."):
        CancelOrderRequest("", OrderType.LIMIT)


def test_candle_requests_need_utc_and_a_forward_range() -> None:
    CandleRequest("NSE:1", Timeframe.M1, NOW, NOW + timedelta(hours=1))
    with pytest.raises(ValueError, match="start"):
        CandleRequest("NSE:1", Timeframe.M1, NOW, NOW)
    with pytest.raises(ValueError, match="UTC"):
        CandleRequest("NSE:1", Timeframe.M1, NOW.astimezone(IST), NOW + timedelta(hours=1))


def test_every_timestamp_in_a_dto_must_be_utc() -> None:
    naive = datetime(2026, 9, 21)
    with pytest.raises(ValueError, match="UTC"):
        BrokerSession("C", naive, NOW)
    with pytest.raises(ValueError, match="UTC"):
        Quote("NSE:1", *(Money.of("1"),) * 5, exchange_ts=NOW.astimezone(IST))
    with pytest.raises(ValueError, match="UTC"):
        BrokerTrade("t", "o", "NSE:1", OrderSide.BUY, 1, Money.of("1"), naive)


def test_an_order_the_platform_cannot_place_is_still_representable_when_listed() -> None:
    """The broker's book can hold orders entered in its app (e.g. market orders): they must
    list, not crash, and never masquerade as one of ours."""
    order = BrokerOrder("9", None, "NSE:3045", OrderSide.SELL, None, 5, 0, BrokerOrderStatus.OPEN)
    assert order.order_type is None and order.client_tag is None
    with pytest.raises(ValueError, match="negative"):
        BrokerOrder(
            "9", None, "NSE:1", OrderSide.BUY, OrderType.LIMIT, -1, 0, BrokerOrderStatus.OPEN
        )


def test_terminal_statuses_and_the_unrecognised_status() -> None:
    assert {s for s in BrokerOrderStatus if s.is_terminal} == {
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELLED,
        BrokerOrderStatus.REJECTED,
    }
    assert not BrokerOrderStatus.UNRECOGNISED.is_terminal  # unknown is never assumed finished


def test_a_trade_needs_a_positive_quantity() -> None:
    with pytest.raises(ValueError, match="positive"):
        BrokerTrade("t", "o", "NSE:1", OrderSide.BUY, 0, Money.of("1"))


def test_market_data_modes_are_a_minimum_richness() -> None:
    assert {m.value for m in MarketDataMode} == {"LTP", "QUOTE"}
