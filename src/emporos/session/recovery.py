"""Start-up recovery: no trading until every doubtful order is resolved and the books reconcile.

Order matters and is the whole point: resolve the orders the last process left in doubt, apply the
fills the broker has, and only then reconcile — a reconciliation run before the fills are in would
report every one of them as a discrepancy. Any failure or discrepancy leaves the session HALTED.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.execution.fills import SyncResult
from emporos.persistence.records import OrderRecord
from emporos.portfolio.reconciliation import Discrepancy, ReconciliationTrigger


class OrderRecovery(Protocol):
    async def recover(self) -> tuple[OrderRecord, ...]: ...


class FillSync(Protocol):
    async def sync(self) -> SyncResult: ...


class AccountReconciler(Protocol):
    async def run(self, trigger: ReconciliationTrigger) -> tuple[Discrepancy, ...]: ...


@dataclass(frozen=True)
class RecoveryResult:
    clean: bool
    orders_checked: int
    fills_applied: int
    problem: str = ""


class StartupRecovery:
    def __init__(
        self,
        orders: OrderRecovery,
        fills: FillSync,
        reconciler: AccountReconciler,
        alerts: AlertSink,
    ) -> None:
        self._orders = orders
        self._fills = fills
        self._reconciler = reconciler
        self._alerts = alerts

    async def run(
        self, trigger: ReconciliationTrigger = ReconciliationTrigger.STARTUP
    ) -> RecoveryResult:
        try:
            checked = await self._orders.recover()
            synced = await self._fills.sync()
            found = await self._reconciler.run(trigger)
        except Exception as error:
            # The reconciler has already halted trading if it was the one that failed.
            self._alerts.raise_alert("recovery_failed", repr(error))
            return RecoveryResult(False, 0, 0, f"{type(error).__name__}: {error}")
        if found:
            return RecoveryResult(
                False, len(checked), synced.applied, f"{len(found)} reconciliation discrepancies"
            )
        return RecoveryResult(True, len(checked), synced.applied)
