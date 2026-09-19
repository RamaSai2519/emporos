"""Broker order updates → the neutral form a strategy is told (`domain.OrderUpdate`).

The execution layer sits between the broker and the strategies: it may see broker DTOs, they may
not. Translating here keeps `emporos.strategies` free of every `emporos.broker` import.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from emporos.broker.models import BrokerOrderStatus, BrokerOrderUpdate
from emporos.domain.order_updates import OrderUpdate, OrderUpdateStatus

_STATUS: Mapping[BrokerOrderStatus, OrderUpdateStatus] = MappingProxyType(
    {
        BrokerOrderStatus.PENDING: OrderUpdateStatus.WORKING,
        BrokerOrderStatus.OPEN: OrderUpdateStatus.WORKING,
        BrokerOrderStatus.TRIGGER_PENDING: OrderUpdateStatus.WORKING,
        BrokerOrderStatus.PARTIALLY_FILLED: OrderUpdateStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED: OrderUpdateStatus.FILLED,
        BrokerOrderStatus.CANCELLED: OrderUpdateStatus.CANCELLED,
        BrokerOrderStatus.REJECTED: OrderUpdateStatus.REJECTED,
        BrokerOrderStatus.UNRECOGNISED: OrderUpdateStatus.UNKNOWN,
    }
)


class OrderUpdateTranslator:
    def translate(self, update: BrokerOrderUpdate) -> OrderUpdate:
        order = update.order
        return OrderUpdate(
            instrument_id=order.instrument_id,
            side=order.side,
            status=_STATUS[order.status],
            quantity=order.quantity,
            filled_quantity=order.filled_quantity,
            ts=update.received_at,
            average_price=order.average_price,
            ordertag=order.client_tag,
            message=order.status_message,
        )
