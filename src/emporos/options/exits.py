"""When an open spread is closed before expiry (EM-226, PROFIT_PLAN.md §5 B1/B2 exits).

A rule looks at one number set: the credit received, what closing costs now, and the days left. The
policy tries its rules in a fixed order and the first that fires decides, so the order is a stated
part of a declaration: the stop is tried first (a bad day is not rescued by a target it also
meets), then the profit target, then the time exit."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

__all__ = [
    "ExitPolicy",
    "ExitReason",
    "ExitRule",
    "MinDaysToExpiry",
    "OpenView",
    "ProfitTarget",
    "StopLoss",
    "standard_policy",
]


class ExitReason(StrEnum):
    STOP_LOSS = "stop_loss"
    PROFIT_TARGET = "profit_target"
    TIME = "time"
    EXPIRY = "expiry"  # settled at expiry: decided by the calendar, not by a rule


@dataclass(frozen=True)
class OpenView:
    credit_per_unit: Decimal  # received when opened
    close_debit_per_unit: Decimal  # what closing costs at today's marks
    days_to_expiry: int

    @property
    def pnl_per_unit(self) -> Decimal:
        return self.credit_per_unit - self.close_debit_per_unit


class ExitRule(Protocol):
    reason: ExitReason

    def fires(self, view: OpenView) -> bool: ...


@dataclass(frozen=True)
class StopLoss:
    """Close when the loss reaches `multiple` times the credit (2 means losing twice the credit)."""

    multiple: Decimal
    reason = ExitReason.STOP_LOSS

    def __post_init__(self) -> None:
        if self.multiple <= 0:
            raise ValueError("the stop multiple must be positive")

    def fires(self, view: OpenView) -> bool:
        return -view.pnl_per_unit >= self.multiple * view.credit_per_unit


@dataclass(frozen=True)
class ProfitTarget:
    """Close when `fraction` of the credit has been kept (0.5 means half of it)."""

    fraction: Decimal
    reason = ExitReason.PROFIT_TARGET

    def __post_init__(self) -> None:
        if not Decimal(0) < self.fraction <= Decimal(1):
            raise ValueError("the profit fraction must be in (0, 1]")

    def fires(self, view: OpenView) -> bool:
        return view.pnl_per_unit >= self.fraction * view.credit_per_unit


@dataclass(frozen=True)
class MinDaysToExpiry:
    """Close once this few calendar days or fewer remain (7 means 'at 7 DTE')."""

    days: int
    reason = ExitReason.TIME

    def __post_init__(self) -> None:
        if self.days < 1:
            raise ValueError("a time exit needs at least one day before expiry")

    def fires(self, view: OpenView) -> bool:
        return view.days_to_expiry <= self.days


class ExitPolicy:
    def __init__(self, rules: Sequence[ExitRule]) -> None:
        self._rules = tuple(rules)

    def decide(self, view: OpenView) -> ExitReason | None:
        for rule in self._rules:
            if rule.fires(view):
                return rule.reason
        return None


def standard_policy(
    target: Decimal = Decimal("0.5"), stop: Decimal = Decimal(2), dte: int = 7
) -> ExitPolicy:
    """PROFIT_PLAN §5: exit at 50% of the credit, at 2x the credit lost, or at 7 DTE."""
    return ExitPolicy([StopLoss(stop), ProfitTarget(target), MinDaysToExpiry(dte)])
