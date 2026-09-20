"""A fresh marketable price for an order that has rested too long, from the live marks."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Protocol

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.execution.ports import TickSizes
from emporos.persistence.records import OrderRecord

_BPS = Decimal(10_000)


class MarkSource(Protocol):
    def marks(self) -> Mapping[str, Money]: ...


class MarkRepriceQuoter:
    """The last traded price moved `step_bps` THROUGH the market (up for a buy, down for a sell),
    rounded to the tick in the marketable direction. No fresh mark, no price: a chase is never
    priced from a guess."""

    def __init__(self, marks: MarkSource, ticks: TickSizes, step_bps: Decimal) -> None:
        if step_bps < 0:
            raise ValueError("a reprice step cannot be negative")
        self._marks = marks
        self._ticks = ticks
        self._step = step_bps / _BPS

    async def candidate(self, order: OrderRecord) -> Money | None:
        mark = self._marks.marks().get(order.instrument_id)
        if mark is None:
            return None
        tick = await self._ticks.tick_size(order.instrument_id)
        buying = order.side is OrderSide.BUY
        moved = mark.amount * ((1 + self._step) if buying else (1 - self._step))
        mode = ROUND_CEILING if buying else ROUND_FLOOR
        return Money((moved / tick.amount).to_integral_value(mode) * tick.amount)
