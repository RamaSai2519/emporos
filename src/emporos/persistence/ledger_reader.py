"""Read-only view of one account's platform books (orders, fills, positions), for reconciliation,
snapshots and the portfolio service. Account-scoped: it cannot see another account's rows."""

from __future__ import annotations

from datetime import datetime

from emporos.core.clock import IST, Clock
from emporos.persistence.records import ExecutionRecord, OrderRecord, PositionRecord
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
)


class PlatformLedger:
    def __init__(
        self,
        account_id: str,
        orders: OrderRepository,
        executions: ExecutionRepository,
        positions: PositionRepository,
        clock: Clock,
    ) -> None:
        self._account_id = account_id
        self._orders = orders
        self._executions = executions
        self._positions = positions
        self._clock = clock

    def _session(self) -> str:
        now: datetime = self._clock.now()
        return now.astimezone(IST).date().isoformat()

    async def orders_this_session(self) -> list[OrderRecord]:
        return await self._orders.for_account_session(self._account_id, self._session())

    async def executions_this_session(self) -> list[ExecutionRecord]:
        return await self._executions.for_account_session(self._account_id, self._session())

    async def all_executions(self) -> list[ExecutionRecord]:
        return await self._executions.for_account(self._account_id)

    async def all_positions(self) -> list[PositionRecord]:
        return await self._positions.all_for_account(self._account_id)

    async def open_positions(self) -> list[PositionRecord]:
        return await self._positions.open_for_account(self._account_id)
