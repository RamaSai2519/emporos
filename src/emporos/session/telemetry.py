"""The worker's own numbers: what the metrics publisher emits and the health endpoint reports.

One class reads the worker's live state (lifecycle, books, marks, health flags, reconciliation) and
answers both questions, so the two can never disagree about, say, how many orders are unresolved.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import Clock
from emporos.domain.ticks import Tick
from emporos.observability.health import HealthReport
from emporos.persistence.records import OrderRecord
from emporos.portfolio.service import PortfolioView
from emporos.risk.approval import RiskRejection
from emporos.risk.snapshot import KillSwitchReading, ReconciliationStatus
from emporos.session.lifecycle import SessionLifecycle, SessionState

_UNRESOLVED = ("UNKNOWN", "PENDING_NEW")


class TickRate:
    """Ticks received in the last minute (a sliding window on the injected clock)."""

    def __init__(self, clock: Clock, window: timedelta = timedelta(minutes=1)) -> None:
        self._clock = clock
        self._window = window
        self._seen: deque[datetime] = deque()

    def on_tick(self, tick: Tick) -> None:
        self._seen.append(self._clock.now())

    def per_minute(self) -> int:
        cutoff = self._clock.now() - self._window
        while self._seen and self._seen[0] < cutoff:
            self._seen.popleft()
        return len(self._seen)


class RejectionCounter:
    """Counts risk rejections as they are logged, passing each on to the real log."""

    def __init__(self, inner: RejectionSink) -> None:
        self._inner = inner
        self.count = 0

    async def record(self, rejection: RiskRejection) -> None:
        await self._inner.record(rejection)
        self.count += 1


class RejectionSink(Protocol):
    async def record(self, rejection: RiskRejection) -> None: ...


class ActiveOrders(Protocol):
    async def active(self) -> list[OrderRecord]: ...


class PortfolioReader(Protocol):
    async def view(self) -> PortfolioView: ...


class Staleness(Protocol):
    def max_age_seconds(self) -> float | None: ...


class SwitchReading(Protocol):
    def reading(self) -> KillSwitchReading: ...


class VenueFlags(Protocol):
    def session_ok(self) -> bool: ...

    def order_feed_ok(self) -> bool: ...


class ReconciliationState(Protocol):
    def status(self) -> ReconciliationStatus: ...


class WorkerTelemetry:
    def __init__(
        self,
        clock: Clock,
        lifecycle: SessionLifecycle,
        orders: ActiveOrders,
        portfolio: PortfolioReader,
        marks: Staleness,
        ticks: TickRate,
        rejections: RejectionCounter,
        venue: VenueFlags,
        switch: SwitchReading,
        reconciliation: ReconciliationState,
        max_staleness: float = 120.0,
    ) -> None:
        self._clock, self._lifecycle, self._orders = clock, lifecycle, orders
        self._portfolio, self._marks, self._ticks = portfolio, marks, ticks
        self._rejections, self._venue, self._switch = rejections, venue, switch
        self._reconciliation, self._max_staleness = reconciliation, max_staleness

    async def sample(self) -> dict[str, float | int | Decimal | None]:
        active = await self._orders.active()
        pnl = (await self._portfolio.view()).valuation
        total = None if pnl.unrealised is None else pnl.realised - pnl.fees + pnl.unrealised
        return {
            "WorkerHeartbeat": 1,
            "TicksPerMinute": self._ticks.per_minute(),
            "MaxDataStalenessSeconds": self._marks.max_age_seconds(),
            "OpenOrders": len(active),
            "UnknownOrders": sum(1 for o in active if o.state in _UNRESOLVED),
            "DailyPnL": None if total is None else total.amount,
            "RiskRejections": self._rejections.count,
            "ReconciliationMismatches": int(
                self._reconciliation.status() is ReconciliationStatus.FAILED
            ),
        }

    async def report(self) -> HealthReport:
        active = await self._orders.active()
        staleness = self._marks.max_age_seconds()
        reconciliation = self._reconciliation.status()
        halted = self._switch.reading().halted
        healthy = (
            self._lifecycle.state in (SessionState.TRADING, SessionState.READY)
            and self._venue.session_ok()
            and self._venue.order_feed_ok()
            and not halted
            and reconciliation is ReconciliationStatus.CLEAN
            and (staleness is None or staleness <= self._max_staleness)
        )
        return HealthReport(
            time=self._clock.now(),
            session_state=self._lifecycle.state.value,
            healthy=healthy,
            broker_session_ok=self._venue.session_ok(),
            order_feed_ok=self._venue.order_feed_ok(),
            kill_switch_halted=halted,
            reconciliation=reconciliation.value,
            max_data_staleness_seconds=staleness,
            open_orders=len(active),
            unknown_orders=sum(1 for o in active if o.state in _UNRESOLVED),
        )
