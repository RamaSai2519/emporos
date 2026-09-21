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
from emporos.session.run_status import RunStatus
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
    name: str = ""


class RunFactory(Protocol):
    """Launches a fresh run of a named strategy (recording it, snapshotting its config)."""

    async def launch(self, name: str) -> ManagedRun: ...


class StrategyHost:
    def __init__(
        self,
        runs: list[ManagedRun],
        events: MarketEvents,
        updates: OrderUpdateRouter,
        orders: OrdersByBrokerId,
        translator: OrderUpdateTranslator,
        factory: RunFactory | None = None,
        status: RunStatus | None = None,
    ) -> None:
        self._factory = factory
        self._status = status
        self._stopped: set[str] = set()
        self._runs = runs
        self._events = events
        self._updates = updates
        self._orders = orders
        self._translator = translator
        self._by_id: Mapping[str, ManagedRun] = {run.run_id: run for run in runs}

    async def start(self) -> None:
        for run in self._runs:
            await run.runner.start()

    async def stop_strategy(self, name: str) -> str:
        """Gracefully stop a strategy: it is told the session is ending and signals no more. Its
        positions are NOT touched — closing them is a separate, explicit command."""
        runs = [r for r in self._runs if r.name == name and r.run_id not in self._stopped]
        if not runs:
            return f"{name} is not running"
        for run in runs:
            await run.runner.end_session()
            await run.runner.shutdown()
            self._stopped.add(run.run_id)
            await self._record_stop(run)
        return f"{name} stopped; its positions were left open"

    async def start_strategy(self, name: str) -> str:
        if any(r.name == name and r.run_id not in self._stopped for r in self._runs):
            return f"{name} is already running"
        if self._factory is None:
            raise ValueError("this deployment cannot launch strategies")
        run = await self._factory.launch(name)
        await run.runner.start()
        self._runs.append(run)
        self._by_id = {r.run_id: r for r in self._runs}
        return f"{name} started as run {run.run_id}"

    async def pump(self) -> None:
        """Deliver what has arrived: order updates first (a fill is known before the next bar)."""
        for update in self._updates.drain():
            await self._deliver_update(update)
        for event in self._events.drain():
            for run in self._runs:
                if run.run_id not in self._stopped:
                    await run.runner.handle(event)

    async def end_session(self) -> None:
        for run in self._runs:
            if run.run_id not in self._stopped:
                await run.runner.end_session()

    async def shutdown(self) -> None:
        for run in self._runs:
            if run.run_id not in self._stopped:
                await run.runner.shutdown()
                await self._record_stop(run)

    async def _record_stop(self, run: ManagedRun) -> None:
        if self._status is not None:
            await self._status.stopped(run.run_id)

    async def _deliver_update(self, update: BrokerOrderUpdate) -> None:
        order = await self._orders.by_broker_order_id(update.order.broker_order_id)
        run = self._by_id.get(order.strategy_run_id or "") if order is not None else None
        if run is not None:
            await run.runner.handle_order_update(self._translator.translate(update))
