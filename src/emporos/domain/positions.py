"""A net holding in one instrument. Positive is long, negative short, zero flat."""

from __future__ import annotations

from dataclasses import dataclass

from emporos.domain.money import Money


@dataclass(frozen=True)
class Position:
    instrument_id: str
    net_quantity: int
    average_price: Money

    @classmethod
    def flat(cls, instrument_id: str) -> Position:
        return cls(instrument_id, 0, Money.zero())

    @property
    def is_flat(self) -> bool:
        return self.net_quantity == 0

    @property
    def is_long(self) -> bool:
        return self.net_quantity > 0
