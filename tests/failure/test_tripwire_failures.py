"""EM-189 — the tripwire under fault: a dropped feed, a stuck order, a restart, and the exit.

The REAL watchdog, detectors, tripwire, kill switch (file sentinel, monitor) and standard rule set
run together; only the broker's silence is scripted. The invariants: an anomaly halts new orders,
the halt survives a restart and places no duplicate order, and an exit is never trapped.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from emporos.core.clock import AsyncioSleeper, FixedClock
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from emporos.marketdata.session import SessionWindow
from emporos.marketdata.staleness import StalenessWatchdog
from emporos.risk.kill_switch import (
    TRIPWIRE_SETTER,
    FileSentinelKillSwitch,
    KillSwitchControl,
    KillSwitchMonitor,
)
from emporos.risk.limits import RiskLimits
from emporos.risk.rules.base import RiskRule
from emporos.risk.standard import StandardRuleSet
from emporos.session.halt import KillSwitchHalt
from emporos.session.tripwire import (
    AnomalyTripwire,
    FeedDroppedDetector,
    OrderUpdateGapDetector,
    UnresolvedUnknownOrderDetector,
)
from tests.support.execution import MemoryOrderJournal
from tests.support.fakes import RecordingAlertSink
from tests.support.records import RecordFactory
from tests.support.risk import NOW, healthy, healthy_system
from tests.support.strategies import make_signal

LIMITS = RiskLimits(
    max_daily_loss=Decimal(1000), max_strategy_loss=Decimal(1000),
    max_position_value=Decimal(50000), max_open_positions=3,
    max_capital_deployed=Decimal(50000), max_order_quantity=500,
    max_price_deviation_pct=Decimal(2), max_spread_bps=Decimal(20),
    duplicate_window_seconds=5, max_orders_per_second=2, max_orders_per_minute=60,
)  # fmt: skip


class Process:
    """One 'run' of the worker's protective machinery over a sentinel that outlives it."""

    def __init__(self, sentinel: Path, journal: MemoryOrderJournal, clock: FixedClock) -> None:
        self.clock = clock
        self.sentinel = FileSentinelKillSwitch(sentinel)
        self.monitor = KillSwitchMonitor([self.sentinel], clock, AsyncioSleeper())
        self.control = KillSwitchControl([self.sentinel], [self.sentinel], clock)
        self.alerts = RecordingAlertSink()
        self.watchdog = StalenessWatchdog(clock, window=SessionWindow())
        self.watchdog.watch(["NSE:3045"])
        self.tripwire = AnomalyTripwire(
            [
                FeedDroppedDetector(self.watchdog, timedelta(seconds=60)),
                UnresolvedUnknownOrderDetector(journal, timedelta(seconds=90)),
                OrderUpdateGapDetector(_Feed(), journal, timedelta(seconds=30)),
            ],
            KillSwitchHalt(self.control, TRIPWIRE_SETTER),
            _Switch(self.monitor), clock, self.alerts,
        )  # fmt: skip

    async def verdicts(self, kind: SignalKind) -> dict[str, bool]:
        """Every standard rule's answer to an order in the current kill-switch state."""
        reading = await self.monitor.refresh()
        snapshot = healthy(system=replace(healthy_system(), kill_switch=reading))
        side = OrderSide.BUY if kind is SignalKind.ENTRY else OrderSide.SELL
        signal = make_signal(kind=kind, side=side)
        rules: list[RiskRule] = StandardRuleSet(LIMITS, SessionWindow()).rules()
        return {r.name: r.evaluate(signal, snapshot).allowed for r in rules}


class _Feed:
    def order_feed_ok(self) -> bool:
        return True


class _Switch:
    def __init__(self, monitor: KillSwitchMonitor) -> None:
        self._monitor = monitor

    async def halted(self) -> bool:
        return (await self._monitor.refresh()).halted


async def test_a_ws_drop_with_an_order_working_halts_new_orders_but_not_the_exit(
    tmp_path: Path,
) -> None:
    clock = FixedClock(NOW)
    journal = MemoryOrderJournal()
    order = RecordFactory().order(state="OPEN", updated_at=NOW)
    journal.orders[order.id] = order
    world = Process(tmp_path / "HALT", journal, clock)
    await world.watchdog.on_disconnected("socket closed mid-order")
    assert await world.tripwire.run_once() is None  # within the allowance

    clock.advance(timedelta(seconds=61))
    anomaly = await world.tripwire.run_once()

    assert anomaly is not None and anomaly.detector == "feed_dropped"
    assert world.alerts.alerts[0][0] == "tripwire_halt"
    entry = await world.verdicts(SignalKind.ENTRY)
    exit_ = await world.verdicts(SignalKind.EXIT)
    assert entry["KillSwitchGuard"] is False
    assert exit_["KillSwitchGuard"] is True
    assert all(exit_.values())  # every standard rule lets the exit through


async def test_an_order_stuck_unknown_beyond_the_limit_halts_trading(tmp_path: Path) -> None:
    clock = FixedClock(NOW)
    journal = MemoryOrderJournal()
    stuck = RecordFactory().order(state="UNKNOWN", updated_at=NOW - timedelta(seconds=120))
    journal.orders[stuck.id] = stuck
    world = Process(tmp_path / "HALT", journal, clock)

    anomaly = await world.tripwire.run_once()

    assert anomaly is not None and anomaly.detector == "unresolved_unknown_order"
    assert (await world.monitor.refresh()).halted


async def test_a_restart_after_a_tripwire_halt_stays_halted_and_places_nothing(
    tmp_path: Path,
) -> None:
    clock = FixedClock(NOW)
    journal = MemoryOrderJournal()
    stuck = RecordFactory().order(state="UNKNOWN", updated_at=NOW - timedelta(seconds=120))
    journal.orders[stuck.id] = stuck
    before = Process(tmp_path / "HALT", journal, clock)
    await before.tripwire.run_once()

    after = Process(tmp_path / "HALT", journal, clock)  # the process died and came back
    entry = await after.verdicts(SignalKind.ENTRY)

    assert (await after.monitor.refresh()).halted
    assert entry["KillSwitchGuard"] is False  # no new order can be approved
    assert list(journal.orders) == [stuck.id]  # and nothing was placed or resent
    assert (await after.verdicts(SignalKind.EXIT))["KillSwitchGuard"] is True


async def test_an_operators_halt_still_blocks_the_exit_too(tmp_path: Path) -> None:
    clock = FixedClock(NOW)
    world = Process(tmp_path / "HALT", MemoryOrderJournal(), clock)
    await world.control.engage("operator drill", "rama")

    assert (await world.verdicts(SignalKind.EXIT))["KillSwitchGuard"] is False
