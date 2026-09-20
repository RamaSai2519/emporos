"""How long an order the broker cannot be found to hold stays UNKNOWN before it is called absent.

Rejecting on one empty answer would be a bet that the broker's order book is instantly consistent.
An absence is only believed after several separate checks spread over a window, and the count is
persisted on the order, so a restart never restarts the wait or shortens it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from emporos.persistence.records import OrderRecord


@dataclass(frozen=True)
class AbsencePolicy:
    confirmations: int = 5
    window: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        if self.confirmations < 1 or self.window <= timedelta(0):
            raise ValueError("an absence needs at least one check and a positive window")

    def confirmed(self, order: OrderRecord, checks: int, now: datetime) -> bool:
        return checks >= self.confirmations and now - order.created_at >= self.window
