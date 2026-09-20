"""What the trades would have netted under other costs: a first-order re-pricing, not a re-run.

Charges are scaled and extra slippage is charged on each side of each trade's notional:
`net = gross - fees x fee_multiplier - (entry + exit notional) x extra_bps / 10,000`. It does not
change which orders would have filled, so it can only answer "does the profit survive dearer (or
cheaper) execution on the trades that happened". That is the question it is used for; a scenario
that changes fills belongs in a fresh backtest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.portfolio import ClosedTrade
from emporos.backtest.robustness.benchmark import CostScenario

_BPS = Decimal(10_000)


@dataclass(frozen=True)
class ScenarioOutcome:
    name: str
    fee_multiplier: Decimal
    extra_slippage_bps: Decimal
    net_pnl: Decimal

    @property
    def profitable(self) -> bool:
        return self.net_pnl > ZERO


class CostSensitivity:
    def evaluate(
        self, trades: Sequence[ClosedTrade], scenarios: Sequence[CostScenario]
    ) -> list[ScenarioOutcome]:
        return [self._outcome(trades, s) for s in scenarios]

    @staticmethod
    def _outcome(trades: Sequence[ClosedTrade], scenario: CostScenario) -> ScenarioOutcome:
        net = ZERO
        for trade in trades:
            traded = trade.entry_notional.amount + trade.exit_price.amount * trade.quantity
            slippage = DecimalMath.divide(traded * scenario.extra_slippage_bps, _BPS)
            net += trade.gross_pnl.amount - trade.fees.amount * scenario.fee_multiplier - slippage
        return ScenarioOutcome(
            scenario.name, scenario.fee_multiplier, scenario.extra_slippage_bps, net
        )
