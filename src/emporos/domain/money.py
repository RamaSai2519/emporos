"""`Money` — an exact rupee amount. Floats never touch money (plan.md §6).

Prices, fees and P&L are all `Money`. It is backed by `Decimal` and refuses
floats at construction, so an inexact value cannot enter the system through a
constructor call; persistence stores it as `Decimal128`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, order=True)
class Money:
    amount: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money needs a Decimal amount, got {type(self.amount).__name__}")
        if not self.amount.is_finite():
            raise ValueError("Money must be finite")

    @classmethod
    def of(cls, value: Decimal | int | str) -> Money:
        """Build from an exact source. Floats are rejected — pass a string instead."""
        if isinstance(value, bool) or not isinstance(value, Decimal | int | str):
            raise TypeError(f"cannot build Money from {type(value).__name__}")
        return cls(Decimal(value))

    @classmethod
    def zero(cls) -> Money:
        return cls(Decimal(0))

    def __add__(self, other: Money) -> Money:
        return Money(self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        return Money(self.amount - other.amount)

    def __neg__(self) -> Money:
        return Money(-self.amount)

    def times(self, factor: Decimal | int) -> Money:
        """Scale by an exact factor (e.g. a quantity)."""
        return Money(self.amount * factor)
