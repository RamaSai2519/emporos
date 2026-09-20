"""The two seams between a strategy's signal and the simulated exchange.

* `SignalGate` — where the risk engine (Phase 11) will sit: "risk gates every order". There is no
  risk engine yet, so `PassThroughGate` lets everything through UNCHANGED. It is a placeholder for
  a seam, not a risk check, and every backtest report says so (`risk_gate: "none"`).
* `OrderPricing` — turns a signal into a limit order. Phase 12's execution engine owns the real
  marketable-limit buffer; `MarketableLimitPricing` is the minimal stand-in: the signal's limit
  moved `limit_buffer_bps` toward the market and rounded to the tick, in the direction that keeps
  the order marketable. Limit orders only: the result is always LIMIT or STOPLOSS_LIMIT.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Protocol

from emporos.backtest.orders import SimOrderRequest
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal

_BPS = Decimal(10_000)


@dataclass(frozen=True)
class GateRejection:
    reason: str


class SignalGate(Protocol):
    def review(self, signal: Signal) -> Signal | GateRejection:
        """The signal to trade (possibly resized), or why it may not be traded."""
        ...


class PassThroughGate:
    """No risk engine exists yet: everything passes, unchanged. See the module docstring."""

    name = "none"

    def review(self, signal: Signal) -> Signal | GateRejection:
        return signal


class TickSizes(Protocol):
    def tick_size(self, instrument_id: str) -> Money: ...


class OrderPricing(Protocol):
    def order_for(self, signal: Signal, tag: str) -> SimOrderRequest: ...


class MarketableLimitPricing:
    def __init__(self, limit_buffer_bps: Decimal, ticks: TickSizes) -> None:
        if limit_buffer_bps < 0:
            raise ValueError("the limit buffer cannot be negative")
        self._buffer = limit_buffer_bps / _BPS
        self._ticks = ticks

    def order_for(self, signal: Signal, tag: str) -> SimOrderRequest:
        tick = self._ticks.tick_size(signal.instrument_id)
        buying = signal.side is OrderSide.BUY
        mode = ROUND_CEILING if buying else ROUND_FLOOR
        limit = signal.limit_price.amount
        if signal.order_type is OrderType.LIMIT:
            limit *= (1 + self._buffer) if buying else (1 - self._buffer)
        trigger = signal.trigger_price
        return SimOrderRequest(
            instrument_id=signal.instrument_id,
            side=signal.side,
            order_type=signal.order_type,
            quantity=signal.quantity,
            limit_price=self._to_tick(limit, tick, mode),
            tag=tag,
            trigger_price=None if trigger is None else self._to_tick(trigger.amount, tick, mode),
        )

    @staticmethod
    def _to_tick(price: Decimal, tick: Money, mode: str) -> Money:
        return Money((price / tick.amount).to_integral_value(mode) * tick.amount)
