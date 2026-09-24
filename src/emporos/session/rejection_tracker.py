"""Counts broker rejections as they are pushed, for the tripwire's rejection-burst detector."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Protocol

from emporos.broker.models import BrokerOrderStatus, BrokerOrderUpdate


class RejectionLog(Protocol):
    def rejections_since(self, since: datetime) -> int: ...


class RejectionTracker:
    """An order-update handler. Keeps the times of the last `capacity` rejections, no more."""

    def __init__(self, capacity: int = 1000) -> None:
        if capacity <= 0:
            raise ValueError("the rejection history needs room for at least one entry")
        self._times: deque[datetime] = deque(maxlen=capacity)

    def on_update(self, update: BrokerOrderUpdate) -> None:
        if update.order.status is BrokerOrderStatus.REJECTED:
            self._times.append(update.received_at)

    def rejections_since(self, since: datetime) -> int:
        return sum(1 for t in self._times if t >= since)
