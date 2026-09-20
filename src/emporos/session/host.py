"""Hosts the running strategies: hands them closed bars and order updates, in one total order.

Each strategy is isolated by its own runner (a handler that raises halts THAT strategy and alerts).
The host adds nothing to that; it only routes: every bar to every run, and each order update to the
run whose order it concerns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from emporos.broker.models import BrokerOrderUpdate
from emporos.domain.order_updates import OrderUpdate
from emporos.execution.order_updates import OrderUpdateTranslator
from emporos.persistence.records import OrderRecord
from emporos.session.updates import OrderUpdateRouter
from emporos.strategies.runner import MarketEvent


class MarketEvents(Protocol):
    def drain(self) -> list[MarketEvent]:
        """Closed bars (and ticks) that arrived since the last call, oldest first."""
        ...


class Runner(Protocol):
    async def start(self) -> None: ...
    async def handle(self, event: MarketEvent) -> None: ...
    async def handle_order_update(self, update: OrderUpdate) -> None: ...
    async def end_session(self) -> None: ...
    async def shutdown(self) -> None: ...


class OrdersByBrokerId(Protocol):
    async def by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None: ...


@dataclass(frozen=True)
class ManagedRun:
    run_id: str
    runner: Runner


class StrategyHost:
    def __init__(
        self,
        runs: list[ManagedRun],
        events: MarketEvents,
        updates: OrderUpdateRouter,
        orders: OrdersByBrokerId,
        translator: OrderUpdateTranslator,
    ) -> None:
        self._runs = runs
        self._events = events
        self._updates = updates
        self._orders = orders
        self._translator = translator
        self._by_id: Mapping[str, ManagedRun] = {run.run_id: run for run in runs}

    async def start(self) -> None:
        for run in self._runs:
            await run.runner.start()

    async def pump(self) -> None:
        """Deliver what has arrived: order updates first (a fill is known before the next bar)."""
        for update in self._updates.drain():
            await self._deliver_update(update)
        for event in self._events.drain():
            for run in self._runs:
                await run.runner.handle(event)

    async def end_session(self) -> None:
        for run in self._runs:
            await run.runner.end_session()

    async def shutdown(self) -> None:
        for run in self._runs:
            await run.runner.shutdown()

    async def _deliver_update(self, update: BrokerOrderUpdate) -> None:
        order = await self._orders.by_broker_order_id(update.order.broker_order_id)
        run = self._by_id.get(order.strategy_run_id or "") if order is not None else None
        if run is not None:
            await run.runner.handle_order_update(self._translator.translate(update))
