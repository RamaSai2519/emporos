"""What an end-of-day fill is assumed to cost beyond the close (EM-226, PROFIT_PLAN.md §3.1).

No bid-ask history exists, so slippage is an ASSUMPTION, fixed here before any result: the benchmark
is one tick plus 0.5% of the premium against us on every leg fill, and the adverse scenario is two
ticks plus 1.5% of the premium with 1.5x the statutory charges. Loosening them needs the
operator."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from emporos.domain.orders import OrderSide

__all__ = ["ADVERSE", "BENCHMARK", "SlippageScenario"]


@dataclass(frozen=True)
class SlippageScenario:
    name: str
    ticks: int
    premium_fraction: Decimal
    fee_multiplier: Decimal

    def __post_init__(self) -> None:
        if self.ticks < 0 or self.premium_fraction < 0:
            raise ValueError("slippage cannot be negative")
        if self.fee_multiplier < 1:
            raise ValueError("a scenario may not charge less than the statutory fees")

    def fill_price(self, side: OrderSide, mark: Decimal, tick: Decimal) -> Decimal:
        """A buy pays above the mark and a sell receives below it, on the tick grid, never below
        0."""
        slip = self.ticks * tick + self.premium_fraction * mark
        if side is OrderSide.BUY:
            return ((mark + slip) / tick).to_integral_value(ROUND_CEILING) * tick
        return max(Decimal(0), ((mark - slip) / tick).to_integral_value(ROUND_FLOOR) * tick)


BENCHMARK = SlippageScenario("benchmark", 1, Decimal("0.005"), Decimal(1))
ADVERSE = SlippageScenario("adverse", 2, Decimal("0.015"), Decimal("1.5"))
