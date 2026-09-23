"""How a signal becomes an order, and how the exchange's answers reach the book and the strategy.

    strategy ─signal─▶ OrderFlow (gate ─▶ pricing ─▶ SimulatedBroker.submit) ─▶ OrderEventQueue
    bar ─▶ SimulatedBroker.match ─────────────────────────────────────────────▶ OrderEventQueue
    OrderEventQueue ─▶ EventSettler ─▶ costs ─▶ portfolio ─▶ strategy.on_order_update

The queue is what lets the strategy's sink exist BEFORE the runner it feeds does (the builder needs
the sink to make the runner, the settler needs the runner): the sink only queues, the settler
drains once the runner exists. A fill is always booked BEFORE the strategy is told about it, so a
handler that asks for its position sees the fill.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.costs import BacktestCosts
from emporos.backtest.journal import BacktestEventSink, NoSink
from emporos.backtest.orders import FillReason, SimEvent
from emporos.backtest.portfolio import BacktestPortfolio
from emporos.backtest.pricing import GateRejection, OrderPricing, SignalGate
from emporos.domain.order_updates import OrderUpdate, OrderUpdateStatus
from emporos.domain.signals import Signal


@dataclass
class RunCounters:
    signals: int = 0
    gate_rejections: int = 0
    orders: int = 0
    exchange_rejections: int = 0
    fills: int = 0
    cancelled_or_expired: int = 0
    refused_after_square_off: int = 0
    square_off_signals: int = 0
    forced_square_offs: int = 0


class OrderEventQueue:
    def __init__(self) -> None:
        self._events: deque[SimEvent] = deque()

    def push(self, event: SimEvent) -> None:
        self._events.append(event)

    def pop(self) -> SimEvent | None:
        return self._events.popleft() if self._events else None


class SessionCutoff(Protocol):
    def blocks(self, signal: Signal) -> bool:
        """True when the session, not the strategy, now owns this instrument's position."""
        ...


class OrderFlow:
    """The strategy's `SignalSink`: cutoff, gate, price, submit. Everything it produces is queued.
    The session's own exits (`submit_system`) take the same route minus the cutoff, so they are
    gated and priced like any other order."""

    def __init__(
        self,
        broker: SimulatedBroker,
        gate: SignalGate,
        pricing: OrderPricing,
        cutoff: SessionCutoff,
        queue: OrderEventQueue,
        counters: RunCounters,
        sink: BacktestEventSink | None = None,
    ) -> None:
        self._broker = broker
        self._gate = gate
        self._pricing = pricing
        self._cutoff = cutoff
        self._queue = queue
        self._counters = counters
        self._sink: BacktestEventSink = sink or NoSink()

    async def submit(self, signal: Signal) -> None:
        self._counters.signals += 1
        self._sink.on_signal(signal, system=False)
        if self._cutoff.blocks(signal):
            self._counters.refused_after_square_off += 1
            return
        self._route(signal)

    async def submit_system(self, signal: Signal) -> None:
        """The session's own exit: counted as a square-off signal, not as the strategy's."""
        self._counters.square_off_signals += 1
        self._sink.on_signal(signal, system=True)
        self._route(signal)

    def _route(self, signal: Signal) -> None:
        verdict = self._gate.review(signal)
        if isinstance(verdict, GateRejection):
            self._counters.gate_rejections += 1
            return
        self._counters.orders += 1
        request = self._pricing.order_for(verdict, f"BT{self._counters.orders:08d}")
        event = self._broker.submit(request)
        self._queue.push(event)
        self._sink.on_order(signal, request, event)


class UpdateReceiver(Protocol):
    """What the settler needs of the strategy runner."""

    async def handle_order_update(self, update: OrderUpdate) -> None: ...


class EventSettler:
    def __init__(
        self,
        queue: OrderEventQueue,
        costs: BacktestCosts,
        portfolio: BacktestPortfolio,
        receiver: UpdateReceiver,
        counters: RunCounters,
        sink: BacktestEventSink | None = None,
    ) -> None:
        self._queue = queue
        self._costs = costs
        self._portfolio = portfolio
        self._receiver = receiver
        self._counters = counters
        self._sink: BacktestEventSink = sink or NoSink()

    async def settle(self) -> None:
        """Drain the queue. A strategy answer to an update may queue more; those are drained too."""
        while (event := self._queue.pop()) is not None:
            self._count(event)
            if event.fill is not None:
                charges = self._costs.charges(event.fill)
                self._portfolio.apply(event.fill, charges.total)
                self._sink.on_fill(event.fill, charges)
            await self._receiver.handle_order_update(event.update)

    def _count(self, event: SimEvent) -> None:
        status = event.update.status
        if event.fill is not None:
            self._counters.fills += 1
            if event.fill.reason is FillReason.FORCED_SQUARE_OFF:
                self._counters.forced_square_offs += 1
        elif status is OrderUpdateStatus.REJECTED:
            self._counters.exchange_rejections += 1
        elif status is OrderUpdateStatus.CANCELLED:
            self._counters.cancelled_or_expired += 1
