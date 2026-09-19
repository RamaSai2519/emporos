"""One fixed Decimal context for every indicator, so results do not depend on thread state."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal

CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


def add(a: Decimal, b: Decimal) -> Decimal:
    return CONTEXT.add(a, b)


def sub(a: Decimal, b: Decimal) -> Decimal:
    return CONTEXT.subtract(a, b)


def mul(a: Decimal, b: Decimal) -> Decimal:
    return CONTEXT.multiply(a, b)


def div(a: Decimal, b: Decimal) -> Decimal:
    return CONTEXT.divide(a, b)


def require_period(period: int) -> int:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("an indicator period must be a positive integer")
    return period
