"""Automatic shutdown on feed and execution anomalies (EM-189, plan.md §11).

    AnomalyDetector (one class per anomaly) ──▶ AnomalyTripwire ──▶ TradingHalt (the kill switch)

Each detector answers one question about one seam ("has the feed been down too long?") and knows
nothing about halting. The tripwire polls them, and on the FIRST anomaly halts trading and raises an
alert. It halts through the same kill switch an operator uses, so the halt is visible to every
process, survives a restart, and is cleared only by `emporos resume`: there is no auto-resume.

A tripwire halt blocks NEW orders and never exits (`KillSwitchGuard` recognises the setter): being
unable to leave a position is worse than the anomaly. The reconciliation mismatch already trips
through the reconciler and is deliberately not duplicated here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.portfolio.reconciliation import TradingHalt
from emporos.session.rejection_tracker import RejectionLog
from emporos.session.square_off import WorkingOrders

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Anomaly:
    detector: str
    reason: str


class AnomalyDetector(Protocol):
    name: str

    async def check(self, now: datetime) -> Anomaly | None: ...


class FeedWatch(Protocol):
    """What the tripwire may ask of the market-data feed. `StalenessWatchdog` provides it."""

    def feed_down_since(self) -> datetime | None:
        """When the feed dropped, judged inside the session only; None when connected."""
        ...

    def watched_count(self) -> int: ...

    def stale_count(self) -> int: ...


class OrderFeedHealth(Protocol):
    def order_feed_ok(self) -> bool: ...


class FeedDroppedDetector:
    name = "feed_dropped"

    def __init__(self, feed: FeedWatch, max_down: timedelta) -> None:
        _positive(max_down, "the feed-drop allowance")
        self._feed = feed
        self._max_down = max_down

    async def check(self, now: datetime) -> Anomaly | None:
        since = self._feed.feed_down_since()
        if since is not None and now - since > self._max_down:
            return Anomaly(
                self.name, f"the market-data feed has been down for {(now - since).seconds}s"
            )
        return None


class WidespreadStalenessDetector:
    name = "widespread_staleness"

    def __init__(self, feed: FeedWatch, max_stale_fraction: Decimal) -> None:
        if not 0 < max_stale_fraction < 1:
            raise ValueError("the stale fraction must be between 0 and 1")
        self._feed = feed
        self._max = max_stale_fraction

    async def check(self, now: datetime) -> Anomaly | None:
        watched = self._feed.watched_count()
        if watched == 0:
            return None
        stale = self._feed.stale_count()
        if Decimal(stale) / Decimal(watched) > self._max:
            return Anomaly(self.name, f"{stale} of {watched} watched instruments are stale")
        return None


class UnresolvedUnknownOrderDetector:
    """An order that has been UNKNOWN too long: the broker has not confirmed or denied it."""

    name = "unresolved_unknown_order"

    def __init__(self, orders: WorkingOrders, max_age: timedelta) -> None:
        _positive(max_age, "the unknown-order allowance")
        self._orders = orders
        self._max_age = max_age

    async def check(self, now: datetime) -> Anomaly | None:
        stuck = [
            o for o in await self._orders.active()
            if o.state == "UNKNOWN" and now - o.updated_at > self._max_age
        ]  # fmt: skip
        if stuck:
            ids = ", ".join(sorted(o.id for o in stuck))
            return Anomaly(self.name, f"order(s) UNKNOWN for over {self._max_age}: {ids}")
        return None


class RejectionBurstDetector:
    name = "rejection_burst"

    def __init__(self, log: RejectionLog, count: int, window: timedelta) -> None:
        if count <= 0:
            raise ValueError("the rejection count must be positive")
        _positive(window, "the rejection window")
        self._log = log
        self._count = count
        self._window = window

    async def check(self, now: datetime) -> Anomaly | None:
        recent = self._log.rejections_since(now - self._window)
        if recent >= self._count:
            return Anomaly(self.name, f"{recent} broker rejections within {self._window}")
        return None


class OrderUpdateGapDetector:
    """The broker's order-update feed has been down while orders are open: their fills would be
    invisible to us."""

    name = "order_update_gap"

    def __init__(self, health: OrderFeedHealth, orders: WorkingOrders, max_gap: timedelta) -> None:
        _positive(max_gap, "the order-update gap")
        self._health = health
        self._orders = orders
        self._max_gap = max_gap
        self._down_since: datetime | None = None

    async def check(self, now: datetime) -> Anomaly | None:
        if self._health.order_feed_ok() or not await self._orders.active():
            self._down_since = None
            return None
        if self._down_since is None:
            self._down_since = now
        if now - self._down_since > self._max_gap:
            return Anomaly(
                self.name,
                f"no order updates for {(now - self._down_since).seconds}s with orders open",
            )
        return None


class SwitchView(Protocol):
    async def halted(self) -> bool: ...


class AnomalyTripwire:
    """Polls its detectors; the first anomaly halts trading once, then it waits for a resume.

    It re-arms only after it has seen the switch set and then clear, so a slow-to-poll kill switch
    monitor cannot make it halt twice, and an operator's resume genuinely re-enables it."""

    def __init__(
        self,
        detectors: Sequence[AnomalyDetector],
        halt: TradingHalt,
        switch: SwitchView,
        clock: Clock,
        alerts: AlertSink,
    ) -> None:
        if not detectors:
            raise ValueError("a tripwire with no detector could never trip")
        self._detectors = tuple(detectors)
        self._halt = halt
        self._switch = switch
        self._clock = clock
        self._alerts = alerts
        self._tripped = False
        self._seen_halted = False

    async def run_once(self) -> Anomaly | None:
        if self._tripped:
            await self._maybe_rearm()
            return None
        now = self._clock.now()
        for detector in self._detectors:
            try:
                anomaly = await detector.check(now)
            except Exception as error:
                # A detector that cannot answer is itself a reason to stop: fail closed.
                anomaly = Anomaly(detector.name, f"the detector failed: {error!r}")
            if anomaly is not None:
                await self._trip(anomaly)
                return anomaly
        return None

    async def _trip(self, anomaly: Anomaly) -> None:
        self._tripped = True
        self._seen_halted = False
        reason = f"tripwire {anomaly.detector}: {anomaly.reason}"
        _LOG.error("halting trading: %s", reason)
        self._alerts.raise_alert("tripwire_halt", reason)
        await self._halt.halt(reason)

    async def _maybe_rearm(self) -> None:
        halted = await self._switch.halted()
        if halted:
            self._seen_halted = True
        elif self._seen_halted:
            self._tripped = False


def _positive(value: timedelta, what: str) -> None:
    if value <= timedelta(0):
        raise ValueError(f"{what} must be positive")
