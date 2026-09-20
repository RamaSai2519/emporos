"""Builders for risk tests: a snapshot in which every rule passes, and one-field variations."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TypeVar

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.domain.signals import Signal
from emporos.domain.trading_mode import TradingMode
from emporos.risk.approval import RiskRejection
from emporos.risk.limits import RiskLimits
from emporos.risk.snapshot import (
    AccountFacts,
    InstrumentMarket,
    KillSwitchReading,
    OrderFlowFacts,
    ReconciliationStatus,
    RiskSnapshot,
    SystemFacts,
    WorkingOrder,
)
from emporos.risk.verdict import RuleVerdict
from tests.support.strategies import INSTRUMENT, T0

_T = TypeVar("_T")

NOW = T0 + timedelta(minutes=30)  # 09:45 IST on a trading Monday: inside the session


def healthy_system(**changes: object) -> SystemFacts:
    base = SystemFacts(
        mode=TradingMode.LIVE,
        live_trading_enabled=True,
        kill_switch=KillSwitchReading(halted=False, known=True, source="test"),
        broker_session_ok=True,
        order_feed_ok=True,
        reconciliation=ReconciliationStatus.CLEAN,
    )
    return _replace(base, changes)


def calm_market(**changes: object) -> InstrumentMarket:
    base = InstrumentMarket(
        ltp=Money.of("100"),
        bid=Money.of("99.95"),
        ask=Money.of("100.05"),
        lower_circuit=Money.of("90"),
        upper_circuit=Money.of("110"),
        stale=False,
    )
    return _replace(base, changes)


def healthy(**changes: object) -> RiskSnapshot:
    """Every rule passes for `make_signal()` in this snapshot."""
    base = RiskSnapshot(
        now=NOW,
        system=healthy_system(),
        account=AccountFacts(),
        flow=OrderFlowFacts(),
        markets={INSTRUMENT: calm_market()},
    )
    return _replace(base, changes)


def long_position(instrument_id: str, quantity: int, price: str) -> Position:
    return Position(instrument_id, quantity, Money.of(price))


def working(side: OrderSide, seconds_ago: float, instrument_id: str = INSTRUMENT) -> WorkingOrder:
    return WorkingOrder(instrument_id, side, NOW - timedelta(seconds=seconds_ago))


def generous_limits(**changes: object) -> RiskLimits:
    values: dict[str, object] = {
        "max_daily_loss": Decimal("2000"),
        "max_strategy_loss": Decimal("1000"),
        "max_position_value": Decimal("25000"),
        "max_open_positions": 3,
        "max_capital_deployed": Decimal("60000"),
        "max_order_quantity": 500,
        "max_price_deviation_pct": Decimal("2"),
        "max_spread_bps": Decimal("20"),
        "duplicate_window_seconds": 5,
        "max_orders_per_second": 2,
        "max_orders_per_minute": 60,
    }
    values.update(changes)
    return RiskLimits.model_validate(values)


def _replace(obj: _T, changes: dict[str, object]) -> _T:
    return replace(obj, **changes)  # type: ignore[type-var]


class StaticSnapshots:
    """A `SnapshotProvider` that returns one snapshot, or fails on demand."""

    def __init__(
        self, snapshot: RiskSnapshot | None = None, error: Exception | None = None
    ) -> None:
        self._snapshot = snapshot
        self._error = error
        self.asked: list[Signal] = []

    async def snapshot(self, signal: Signal) -> RiskSnapshot:
        self.asked.append(signal)
        if self._error is not None:
            raise self._error
        assert self._snapshot is not None
        return self._snapshot


class MemoryRejectionLog:
    """A `RejectionLog` that keeps rejections in memory, or fails on demand."""

    def __init__(self, error: Exception | None = None) -> None:
        self.rejections: list[RiskRejection] = []
        self.calls: list[str] = []
        self._error = error

    async def record(self, rejection: RiskRejection) -> None:
        self.calls.append("record")
        if self._error is not None:
            raise self._error
        self.rejections.append(rejection)


class ScriptedRule:
    """A rule that says what it is told, and remembers being asked."""

    def __init__(
        self,
        name: str,
        allow: bool = True,
        error: Exception | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.name = name
        self._allow = allow
        self._error = error
        self._log = log if log is not None else []

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        self._log.append(self.name)
        if self._error is not None:
            raise self._error
        return RuleVerdict.allow() if self._allow else RuleVerdict.block(f"{self.name} says no")
