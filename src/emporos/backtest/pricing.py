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
from decimal import Decimal
from typing import Protocol

from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.orders import SimOrderRequest
from emporos.backtest.portfolio import BacktestPortfolio
from emporos.core.clock import Clock
from emporos.domain.marketable import MarketableLimit
from emporos.domain.money import Money
from emporos.domain.signals import Signal


@dataclass(frozen=True)
class GateRejection:
    reason: str


@dataclass(frozen=True)
class GateContext:
    """What a gate may look at: the run's clock, its simulated book and the orders resting in it.

    Handed to the gate FACTORY once per run, so a gate can judge a signal against the state the
    strategy actually created (its positions, its P&L) without the engine knowing what it needs.
    """

    clock: Clock
    portfolio: BacktestPortfolio
    broker: SimulatedBroker


class SignalGate(Protocol):
    name: str  # what a report calls it: "none" when nothing is checked

    def review(self, signal: Signal) -> Signal | GateRejection:
        """The signal to trade (possibly resized), or why it may not be traded."""
        ...


class PassThroughGate:
    """No risk checking: everything passes, unchanged. See the module docstring."""

    name = "none"

    def __init__(self, context: GateContext | None = None) -> None:
        del context  # a pass-through looks at nothing

    def review(self, signal: Signal) -> Signal | GateRejection:
        return signal


class TickSizes(Protocol):
    def tick_size(self, instrument_id: str) -> Money: ...


class OrderPricing(Protocol):
    def order_for(self, signal: Signal, tag: str) -> SimOrderRequest: ...


class MarketableLimitPricing:
    def __init__(self, limit_buffer_bps: Decimal, ticks: TickSizes) -> None:
        self._rule = MarketableLimit(limit_buffer_bps)
        self._ticks = ticks

    def order_for(self, signal: Signal, tag: str) -> SimOrderRequest:
        prices = self._rule.prices(signal, self._ticks.tick_size(signal.instrument_id))
        return SimOrderRequest(
            instrument_id=signal.instrument_id,
            side=signal.side,
            order_type=signal.order_type,
            quantity=signal.quantity,
            limit_price=prices.limit,
            tag=tag,
            strategy_run_id=signal.strategy_run_id,
            trigger_price=prices.trigger,
        )
