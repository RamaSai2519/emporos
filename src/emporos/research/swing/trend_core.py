"""C1: the ETF trend core (config/experiments/c1-etf-trend-core.yaml, EM-237).

Every rule is a line of the declaration; nothing is added and nothing is tuned. At the close of the
first session of each calendar month the policy says how much of the book each ETF should be:

* the trend test: the asset's close is above its 200-session simple average;
* `top2`: score = mean of the 3-, 6- and 12-month returns (63, 126 and 252 sessions); the 2 best
  assets that pass the trend test AND have score > 0, an equal half each; the rest is cash;
* `all_equal`: every asset that passes the trend test, an equal third of the book each;
* `fixed_40_30_30`: NIFTYBEES 40%, JUNIORBEES 30%, GOLDBEES 30%, each only while it passes the trend
  test, else its share sits in cash.

Nothing is decided until every asset has 253 sessions of history. The book (`TargetWeightBook`)
trades the difference from these weights at the next session's close.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.research.swing.data import AsOfView
from emporos.research.swing.rules import DecisionContext, Intent

__all__ = ["RULES", "TrendCorePolicy", "TrendCoreRecord", "blend_score", "passes_trend"]

RULES = ("top2", "all_equal", "fixed_40_30_30")
HISTORY = 253
SMA_WINDOW = 200
TOP_K = 2


def passes_trend(closes: Sequence[Candle]) -> bool:
    last = closes[-1].close.amount
    return last > sum((c.close.amount for c in closes[-SMA_WINDOW:]), Decimal(0)) / SMA_WINDOW


def blend_score(closes: Sequence[Candle]) -> Decimal:
    c = [bar.close.amount for bar in closes]
    return sum((c[-1] / c[-1 - back] - 1 for back in (63, 126, 252)), Decimal(0)) / 3


@dataclass
class TrendCoreRecord:
    first_ranked_day: date | None = None
    decisions: int = 0
    months_all_cash: int = 0
    months_held: dict[str, int] = field(default_factory=dict)


class TrendCorePolicy:
    """`fixed` gives the fixed_40_30_30 weights by instrument id (required for that rule)."""

    def __init__(
        self, rule: str, assets: Sequence[str], fixed: Mapping[str, Decimal] | None = None
    ) -> None:
        if rule not in RULES:
            raise ValueError(f"the rule is one of {RULES}")
        if not assets:
            raise ValueError("the core holds at least one asset")
        if rule == "fixed_40_30_30" and (
            fixed is None or set(fixed) != set(assets) or sum(fixed.values(), Decimal(0)) != 1
        ):
            raise ValueError("fixed weights cover every asset and add up to 1")
        self._rule = rule
        self._assets = tuple(assets)
        self._fixed = dict(fixed or {})
        self.record = TrendCoreRecord()

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        """A `SwingStrategy` in name only: the book that runs this policy sizes by weight, so the
        arm's outcome can carry it and its record."""
        return ()

    def targets(self, view: AsOfView) -> Mapping[str, Decimal] | None:
        histories = {n: view.history(n, HISTORY) for n in self._assets}
        if any(len(h) < HISTORY for h in histories.values()):
            return None
        if self.record.first_ranked_day is None:
            self.record.first_ranked_day = view.day
        self.record.decisions += 1
        weights = self._weights(histories)
        if not weights:
            self.record.months_all_cash += 1
        for name in weights:
            self.record.months_held[name] = self.record.months_held.get(name, 0) + 1
        return weights

    def _weights(self, histories: Mapping[str, Sequence[Candle]]) -> dict[str, Decimal]:
        trending = [n for n in self._assets if passes_trend(histories[n])]
        if self._rule == "all_equal":
            return {n: Decimal(1) / len(self._assets) for n in trending}
        if self._rule == "fixed_40_30_30":
            return {n: self._fixed[n] for n in trending}
        ranked = sorted(
            ((blend_score(histories[n]), n) for n in trending), key=lambda p: (-p[0], p[1])
        )
        chosen = [n for score, n in ranked if score > 0][:TOP_K]
        return {n: Decimal(1) / TOP_K for n in chosen}
