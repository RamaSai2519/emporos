from datetime import UTC, datetime

import pytest

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerOrderUpdate
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdateStatus
from emporos.domain.orders import OrderSide, OrderType
from emporos.execution.order_updates import OrderUpdateTranslator

RECEIVED = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _update(status: BrokerOrderStatus, filled: int = 0) -> BrokerOrderUpdate:
    order = BrokerOrder(
        broker_order_id="B1",
        client_tag="Sabc",
        instrument_id="NSE:1001",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=10,
        filled_quantity=filled,
        status=status,
        price=Money.of("100"),
        average_price=Money.of("100.5"),
        status_message="msg",
    )
    return BrokerOrderUpdate(order, RECEIVED)


def test_every_broker_status_has_a_strategy_facing_status() -> None:
    translator = OrderUpdateTranslator()
    for status in BrokerOrderStatus:
        assert translator.translate(_update(status)).status in OrderUpdateStatus


@pytest.mark.parametrize(
    ("broker", "expected"),
    [
        (BrokerOrderStatus.PENDING, OrderUpdateStatus.WORKING),
        (BrokerOrderStatus.OPEN, OrderUpdateStatus.WORKING),
        (BrokerOrderStatus.TRIGGER_PENDING, OrderUpdateStatus.WORKING),
        (BrokerOrderStatus.PARTIALLY_FILLED, OrderUpdateStatus.PARTIALLY_FILLED),
        (BrokerOrderStatus.FILLED, OrderUpdateStatus.FILLED),
        (BrokerOrderStatus.CANCELLED, OrderUpdateStatus.CANCELLED),
        (BrokerOrderStatus.REJECTED, OrderUpdateStatus.REJECTED),
        (BrokerOrderStatus.UNRECOGNISED, OrderUpdateStatus.UNKNOWN),
    ],
)
def test_statuses_map_without_guessing(
    broker: BrokerOrderStatus, expected: OrderUpdateStatus
) -> None:
    assert OrderUpdateTranslator().translate(_update(broker)).status is expected


def test_the_translation_keeps_what_a_strategy_needs_and_nothing_of_the_broker() -> None:
    update = OrderUpdateTranslator().translate(_update(BrokerOrderStatus.PARTIALLY_FILLED, 4))

    assert (update.instrument_id, update.side, update.quantity, update.filled_quantity) == (
        "NSE:1001",
        OrderSide.SELL,
        10,
        4,
    )
    assert update.ts == RECEIVED and update.ordertag == "Sabc"
    assert update.average_price == Money.of("100.5") and update.message == "msg"
    assert not hasattr(update, "broker_order_id") and not hasattr(update, "order")
