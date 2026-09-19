"""What a strategy is told about its orders — deliberately not a broker object.

A strategy learns that an order it caused is working, filled, cancelled or rejected; it never
holds the order, the broker's id, or any way back to the broker. The execution layer translates
broker updates into this neutral form.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide


class OrderUpdateStatus(StrEnum):
    WORKING = "WORKING"  # accepted and live (including waiting for a stop trigger)
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"  # the outcome could not be established; never guessed as anything else

    @property
    def is_terminal(self) -> bool:
        return self in {self.FILLED, self.CANCELLED, self.REJECTED}


@dataclass(frozen=True)
class OrderUpdate:
    instrument_id: str
    side: OrderSide
    status: OrderUpdateStatus
    quantity: int
    filled_quantity: int
    ts: datetime
    average_price: Money | None = None
    ordertag: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() != UTC.utcoffset(None):
            raise ValueError("order update ts must be timezone-aware UTC")
        if self.quantity < 0 or self.filled_quantity < 0:
            raise ValueError("quantities cannot be negative")
        if self.filled_quantity > self.quantity:
            raise ValueError("an order cannot be filled beyond its quantity")
