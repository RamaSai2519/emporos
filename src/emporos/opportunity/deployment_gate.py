"""Conservative initial allocation by deployment stage (EM-152 / EM-164).

A strategy earns its way to full production sizing. `DeploymentGate` reads each allocation's
strategy's `DeploymentStatus` (EM-155's registry metadata — set by the graduation pipeline,
EM-166) and:

* CANDIDATE / PAPER / RETIRED — not eligible for real capital at all. Dropped entirely: reaching
  live capital requires the graduation gate to have moved the strategy to at least
  LIVE_CONSERVATIVE first.
* LIVE_CONSERVATIVE — sized down to a fraction of what the allocator gave it (default 20%),
  rounded down; an allocation that rounds to zero shares is dropped rather than sent as a
  zero-quantity order.
* PRODUCTION — unchanged.

This is deliberately NOT wired into every pipeline: a backtest or paper-trading run studying a
strategy's true behavior should see its full, unthrottled sizing, so `OpportunityRunner` only
applies a gate when one is explicitly injected — which a live composition root does, and a
backtest or paper-evaluation root does not.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_DOWN, Decimal

from emporos.opportunity.allocator import Allocation
from emporos.strategies.metadata import DeploymentStatus
from emporos.strategies.registry import StrategyRegistry

DEFAULT_CONSERVATIVE_FRACTION = Decimal("0.2")
_INELIGIBLE = frozenset(
    {DeploymentStatus.CANDIDATE, DeploymentStatus.PAPER, DeploymentStatus.RETIRED}
)


class DeploymentGate:
    def __init__(
        self,
        registry: StrategyRegistry,
        conservative_fraction: Decimal = DEFAULT_CONSERVATIVE_FRACTION,
    ) -> None:
        if not (Decimal(0) < conservative_fraction <= Decimal(1)):
            raise ValueError("conservative_fraction must be between 0 (exclusive) and 1")
        self._registry = registry
        self._fraction = conservative_fraction

    def apply(self, allocations: tuple[Allocation, ...]) -> tuple[Allocation, ...]:
        scaled: list[Allocation] = []
        for allocation in allocations:
            status = self._registry.metadata(allocation.candidate.strategy_name).deployment_status
            outcome = self._scale(allocation, status)
            if outcome is not None:
                scaled.append(outcome)
        return tuple(scaled)

    def _scale(self, allocation: Allocation, status: DeploymentStatus) -> Allocation | None:
        if status in _INELIGIBLE:
            return None
        if status is not DeploymentStatus.LIVE_CONSERVATIVE:
            return allocation  # PRODUCTION: full size
        quantity = int(
            (Decimal(allocation.quantity) * self._fraction).to_integral_value(rounding=ROUND_DOWN)
        )
        if quantity <= 0:
            return None
        return replace(allocation, quantity=quantity)
