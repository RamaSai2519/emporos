"""Finds the resting orders that are due a reprice and hands each to the coordinator.

Nothing is remembered in memory that matters: which orders are due, how many reprices they have had
and what they started at are all read from the persisted order (`created_at`, `reprice_count`,
`original_limit_price`), so the schedule survives a restart unchanged. The one thing kept is which
orders were already reported as exhausted, purely so an alert is not repeated every pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.domain.money import Money
from emporos.domain.orders import OrderType
from emporos.execution.repricing import RepricePolicy
from emporos.execution.state import OrderState
from emporos.persistence.records import OrderRecord
from emporos.risk.approval import RiskRejection

_RESTING = (OrderState.OPEN, OrderState.PARTIALLY_FILLED)


class RepriceQuoter(Protocol):
    async def candidate(self, order: OrderRecord) -> Money | None:
        """A fresh marketable price for this order, or None when there is no current market."""
        ...


class ActiveOrders(Protocol):
    async def active(self) -> list[OrderRecord]: ...


class Repricer(Protocol):
    async def reprice(
        self, order: OrderRecord, candidate: Money
    ) -> OrderRecord | RiskRejection: ...


@dataclass(frozen=True)
class RepriceOutcome:
    order_id: str
    result: str  # the new order's id, or why nothing was replaced


class RepriceScheduler:
    def __init__(
        self,
        orders: ActiveOrders,
        repricer: Repricer,
        policy: RepricePolicy,
        quoter: RepriceQuoter,
        clock: Clock,
        alerts: AlertSink,
    ) -> None:
        self._orders = orders
        self._repricer = repricer
        self._policy = policy
        self._quoter = quoter
        self._clock = clock
        self._alerts = alerts
        self._reported: set[str] = set()

    async def run_once(self) -> list[RepriceOutcome]:
        outcomes: list[RepriceOutcome] = []
        for order in await self._orders.active():
            if not self._due(order):
                continue
            candidate = await self._quoter.candidate(order)
            if candidate is None:
                continue
            try:
                result = await self._repricer.reprice(order, candidate)
            except ValueError as refused:
                self._exhausted(order, str(refused))
                outcomes.append(RepriceOutcome(order.id, f"refused: {refused}"))
                continue
            outcomes.append(RepriceOutcome(order.id, self._describe(result)))
        return outcomes

    def _due(self, order: OrderRecord) -> bool:
        return (
            order.state in _RESTING
            and order.order_type == OrderType.LIMIT
            and order.signal_kind is not None
            and order.reprice_count < self._policy.max_reprices
            and self._clock.now() - order.created_at >= self._policy.after
        )

    def _exhausted(self, order: OrderRecord, why: str) -> None:
        if order.id not in self._reported:
            self._reported.add(order.id)
            self._alerts.raise_alert("reprice_refused", f"order {order.id}: {why}")

    @staticmethod
    def _describe(result: OrderRecord | RiskRejection) -> str:
        if isinstance(result, RiskRejection):
            return f"risk rejected the replacement: {result.rule}"
        return result.id
