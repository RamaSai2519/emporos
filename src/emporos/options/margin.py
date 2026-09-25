"""A margin estimate for a defined-risk spread (EM-226).

Exchanges margin a hedged spread near its worst-case loss. `SpanLikeMargin` blocks that loss plus a
buffer. It is an ESTIMATE, not the broker's number: nothing here has been reconciled against a real
margin statement, so every result carries that caveat (PROFIT_PLAN §3.5)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from emporos.options.spread import SpreadPlan

__all__ = ["MarginEstimator", "SpanLikeMargin", "max_loss"]


def max_loss(plan: SpreadPlan, credit_per_unit: Decimal, units: int) -> Decimal:
    """The most the position can lose at expiry, before costs: the wider wing less the credit."""
    return (plan.max_width - credit_per_unit) * units


class MarginEstimator(Protocol):
    def required(self, plan: SpreadPlan, credit_per_unit: Decimal, units: int) -> Decimal: ...


@dataclass(frozen=True)
class SpanLikeMargin:
    buffer: Decimal = Decimal("0.10")  # an assumption, unreconciled

    def __post_init__(self) -> None:
        if self.buffer < 0:
            raise ValueError("the margin buffer cannot be negative")

    def required(self, plan: SpreadPlan, credit_per_unit: Decimal, units: int) -> Decimal:
        return max_loss(plan, credit_per_unit, units) * (1 + self.buffer)
