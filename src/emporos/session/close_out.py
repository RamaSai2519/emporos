"""End of session: last fills, doubtful orders, reconciliation, the day's snapshot."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.domain.money import Money
from emporos.execution.fills import SyncResult
from emporos.persistence.records import OrderRecord, PortfolioSnapshotRecord
from emporos.portfolio.reconciliation import Discrepancy, ReconciliationTrigger
from emporos.portfolio.snapshots import SnapshotKind


class FillSync(Protocol):
    async def sync(self) -> SyncResult: ...


class UnresolvedOrders(Protocol):
    async def resolve_unresolved(self) -> tuple[OrderRecord, ...]: ...


class AccountReconciler(Protocol):
    async def run(self, trigger: ReconciliationTrigger) -> tuple[Discrepancy, ...]: ...


class Snapshots(Protocol):
    async def take(self, kind: SnapshotKind) -> PortfolioSnapshotRecord: ...


@dataclass(frozen=True)
class CloseOutResult:
    discrepancies: int
    snapshot: PortfolioSnapshotRecord | None
    realised_pnl: Money | None
    trades: int | None


class CloseOutHook(Protocol):
    """Read-only follow-up work once the day's books are final (EM-185: the parity report).

    A hook runs after the snapshot, holds no broker, and is isolated: whatever it does, the
    session's close-out is unaffected."""

    name: str

    async def after_close_out(self, result: CloseOutResult) -> None: ...


class EndOfDay:
    def __init__(
        self,
        fills: FillSync,
        orders: UnresolvedOrders,
        reconciler: AccountReconciler,
        snapshots: Snapshots,
        alerts: AlertSink,
        hooks: Sequence[CloseOutHook] = (),
    ) -> None:
        self._fills = fills
        self._orders = orders
        self._reconciler = reconciler
        self._snapshots = snapshots
        self._alerts = alerts
        self._hooks = tuple(hooks)

    async def close_out(self) -> CloseOutResult:
        await self._fills.sync()
        await self._orders.resolve_unresolved()
        found = await self._reconciler.run(ReconciliationTrigger.EOD)
        snapshot: PortfolioSnapshotRecord | None = None
        try:
            snapshot = await self._snapshots.take(SnapshotKind.EOD)
        except Exception as error:
            self._alerts.raise_alert("eod_snapshot_failed", repr(error))
        result = CloseOutResult(
            len(found),
            snapshot,
            None if snapshot is None else snapshot.realised_pnl,
            None if snapshot is None else snapshot.trades,
        )
        for hook in self._hooks:
            try:
                await hook.after_close_out(result)
            except Exception as error:
                self._alerts.raise_alert(f"close_out_hook_failed:{hook.name}", repr(error))
        return result
