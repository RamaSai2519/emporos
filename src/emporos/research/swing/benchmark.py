"""The same-universe equal-weight buy-and-hold, net of the same costs (PROFIT_PLAN §2.5, EM-223).

Today's constituents, projected back, are flattered by survivorship (they are the names that grew
into the index). A rule long those names looks good for that reason alone. So every arm is judged
against the equal-weight buy-and-hold of the SAME names over the SAME days, which carries the same
bias and the same costs: what is left over is the rule's own contribution.

It is run through the same simulator, with a strategy that wants every name that trades and never
lets go, so its costs are the strategy's costs: the schedule, the slippage, the final liquidation.
The book is sized at `notional_per_name` a name (default Rs 50,000, the plan's typical position),
which makes its fees a smaller share than a Rs 1,00,000 book's would be: a stricter benchmark, not a
looser one. A name that starts trading later is bought when it does, at an equal slot.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from emporos.research.swing.costs import SwingCostModel
from emporos.research.swing.data import SwingDataset
from emporos.research.swing.rules import DecisionContext, Intent, Membership
from emporos.research.swing.simulator import SwingConfig, SwingRun, SwingSimulator

__all__ = ["EqualWeightBenchmark", "HoldEverything"]

DEFAULT_NOTIONAL_PER_NAME = Decimal(50_000)


class HoldEverything:
    """Wants every name that trades today, and every name it already holds."""

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        return [Intent(n) for n in sorted(context.tradable | set(context.holdings))]


class EqualWeightBenchmark:
    def __init__(
        self,
        dataset: SwingDataset,
        costs: SwingCostModel,
        membership: Membership | None = None,
        notional_per_name: Decimal = DEFAULT_NOTIONAL_PER_NAME,
    ) -> None:
        if notional_per_name <= 0:
            raise ValueError("a benchmark slot is positive")
        self._dataset = dataset
        self._costs = costs
        self._membership = membership
        self._notional = notional_per_name

    def run(self) -> SwingRun:
        names = len(self._dataset.instrument_ids)
        if names == 0:
            raise ValueError("a benchmark needs at least one name")
        config = SwingConfig(self._notional * names, names)
        return SwingSimulator(
            self._dataset, HoldEverything(), config, self._costs, self._membership
        ).run()
