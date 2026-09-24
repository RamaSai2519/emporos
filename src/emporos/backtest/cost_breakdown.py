"""What a set of round trips cost, split into the parts a reviewer asks about (EM-188).

`ClosedTrade.fees` is one lump, so the split is the cost model's own decomposition of each round
trip at its OWN quantity and price: the same `PortfolioCostModel` the cost-error gate reads, so the
breakdown a report shows and the evidence a verdict used cannot disagree about what a trade costs.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.portfolio import ClosedTrade
from emporos.backtest.robustness.portfolio_economics import PortfolioCostModel
from emporos.domain.instruments import Exchange
from emporos.domain.research_experiments import CostBreakdown


class CostBreakdownCalculator:
    """Every trade is costed against `Exchange.NSE`: this platform trades NSE cash equities only."""

    def __init__(self, model: PortfolioCostModel) -> None:
        self._model = model

    def of(self, trades: Sequence[ClosedTrade]) -> CostBreakdown | None:
        """None when there is nothing to cost: no trades, so no per-trade figure to state."""
        priced = [
            self._model.components_for(Exchange.NSE, t.quantity, t.entry_price)
            for t in trades
            if t.quantity > 0 and t.entry_price.amount > ZERO
        ]
        if not priced:
            return None
        brokerage = sum((c.brokerage.amount for c in priced), ZERO)
        statutory = sum((c.statutory.amount for c in priced), ZERO)
        spread = sum((c.spread.amount for c in priced), ZERO)
        slippage = sum((c.slippage.amount for c in priced), ZERO)
        total = brokerage + statutory + spread + slippage
        return CostBreakdown(
            brokerage=brokerage,
            statutory=statutory,
            spread=spread,
            slippage=slippage,
            total=total,
            per_trade_bps=DecimalMath.mean([c.total_bps for c in priced]),
            per_trade_inr=DecimalMath.divide(total, Decimal(len(priced))),
        )
