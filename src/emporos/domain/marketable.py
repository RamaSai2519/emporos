"""Turning the price a strategy has in mind into a limit order that can actually trade.

Market orders do not exist (Decision 8), so an order that must get done is a limit placed a little
THROUGH the market: a buy above, a sell below, rounded to the tick in the direction that keeps it
marketable. Pure arithmetic shared by the execution engine and the backtest, so both price alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal

_BPS = Decimal(10_000)


@dataclass(frozen=True)
class OrderPrices:
    limit: Money
    trigger: Money | None


class MarketableLimit:
    def __init__(self, buffer_bps: Decimal) -> None:
        if buffer_bps < 0:
            raise ValueError("the limit buffer cannot be negative")
        self._buffer = buffer_bps / _BPS

    def prices(self, signal: Signal, tick: Money) -> OrderPrices:
        if tick <= Money.zero():
            raise ValueError("a tick size must be positive")
        buying = signal.side is OrderSide.BUY
        mode = ROUND_CEILING if buying else ROUND_FLOOR
        limit = signal.limit_price.amount
        if signal.order_type is OrderType.LIMIT:
            limit *= (1 + self._buffer) if buying else (1 - self._buffer)
        trigger = signal.trigger_price
        return OrderPrices(
            limit=self._to_tick(limit, tick, mode),
            trigger=None if trigger is None else self._to_tick(trigger.amount, tick, mode),
        )

    @staticmethod
    def _to_tick(price: Decimal, tick: Money, mode: str) -> Money:
        return Money((price / tick.amount).to_integral_value(mode) * tick.amount)
