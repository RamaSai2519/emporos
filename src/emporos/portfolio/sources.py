"""Where a reconciliation gets its two sides: our books, and what the broker says."""

from __future__ import annotations

from typing import Protocol

from emporos.broker.models import BrokerOrder, BrokerPosition, BrokerTrade
from emporos.persistence.records import ExecutionRecord, OrderRecord, PositionRecord
from emporos.portfolio.reconciliation import ReconciliationSnapshot


class LedgerReader(Protocol):
    async def orders_this_session(self) -> list[OrderRecord]: ...
    async def executions_this_session(self) -> list[ExecutionRecord]: ...
    async def all_executions(self) -> list[ExecutionRecord]: ...
    async def all_positions(self) -> list[PositionRecord]: ...


class BrokerBooks(Protocol):
    async def get_order_book(self) -> list[BrokerOrder]: ...
    async def get_trade_book(self) -> list[BrokerTrade]: ...
    async def get_positions(self) -> list[BrokerPosition]: ...


class BrokerReconciliationSource:
    """Reads both sides at (nearly) the same moment. Our books are read FIRST: a fill that lands
    at the broker between the two reads then shows as "the broker is ahead", which fill adoption is
    built to explain, rather than as a persisted fill the broker "does not have", which would halt
    trading over nothing."""

    def __init__(self, ledger: LedgerReader, broker: BrokerBooks) -> None:
        self._ledger = ledger
        self._broker = broker

    async def snapshot(self) -> ReconciliationSnapshot:
        orders = tuple(await self._ledger.orders_this_session())
        executions = tuple(await self._ledger.executions_this_session())
        positions = tuple(await self._ledger.all_positions())
        history = tuple(await self._ledger.all_executions())
        return ReconciliationSnapshot(
            orders=orders,
            executions=executions,
            positions=positions,
            broker_orders=tuple(await self._broker.get_order_book()),
            broker_trades=tuple(await self._broker.get_trade_book()),
            broker_positions=tuple(await self._broker.get_positions()),
            history=history,
        )
