"""Orders come first: the recorder asks whether an order call is in flight and stands aside."""

from __future__ import annotations

from typing import Protocol

__all__ = ["NoPendingOrders", "OrderPriority"]


class OrderPriority(Protocol):
    def orders_pending(self) -> bool:
        """True while an order call is on its way to the broker."""
        ...


class NoPendingOrders:
    """For a process that places no orders at all."""

    def orders_pending(self) -> bool:
        return False
