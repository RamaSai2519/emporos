"""A1: momentum with a trend filter (config/experiments/a1-momentum-trend-filter.yaml, EM-228).

Every rule below is a line of the declaration; nothing is added and nothing is tuned.

* Signal on analysis closes: `close[t-21] / close[t-L-21] - 1` (the last month skipped), for a
  lookback `L`. A name needs `L + 22` sessions of history to be ranked; a name with momentum of zero
  or less is not in the ranking at all.
* Regime: NIFTY 50 above its 200-session average at the decision close, else the strategy wants
  nothing (every holding sold, no entries).
* Rebalance on the declared calendar (first session of the week or month): keep a holding while it
  ranks in the top `2N`, sell it otherwise, and fill empty slots from the top of the ranking, up to
  `N` names in all. A holding that does not trade on a rebalance session is not ranked and is sold.
* Between rebalances the book is left alone except for the stop: a holding whose loss has reached
  2% of the book (`LossStop`) is sold at the next open. A stopped name may be bought again at a
  later rebalance if it ranks.

The strategy records what it did (`first_ranked_day`, `rebalances`) for the report; that record is
not read by the strategy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.research.swing.regime import RebalanceCalendar
from emporos.research.swing.rules import DecisionContext, Intent, LossStop, RegimeFilter

__all__ = ["MomentumTrend", "MomentumRecord"]

SKIP_SESSIONS = 21


@dataclass
class MomentumRecord:
    first_ranked_day: date | None = None
    rebalances: int = 0  # rebalance sessions on which the regime was on and a ranking was made
    stops: int = 0


class MomentumTrend:
    def __init__(
        self,
        lookback: int,
        max_positions: int,
        calendar: RebalanceCalendar,
        regime: RegimeFilter,
        stop: LossStop,
    ) -> None:
        if lookback < 1 or max_positions < 1:
            raise ValueError("lookback and max positions are positive")
        self._lookback = lookback
        self._n = max_positions
        self._calendar = calendar
        self._regime = regime
        self._stop = stop
        self.record = MomentumRecord()

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        if not self._regime.is_on(context):
            return []
        kept = self._unstopped(context)
        if not self._calendar.is_rebalance(context.view):
            return [Intent(n) for n in kept]
        ranking = self._ranking(context)
        if ranking and self.record.first_ranked_day is None:
            self.record.first_ranked_day = context.day
        self.record.rebalances += bool(ranking)
        top_2n = set(ranking[: 2 * self._n])
        keep = [n for n in ranking if n in kept and n in top_2n]
        fill = [n for n in ranking if n not in keep][: self._n - len(keep)]
        return [Intent(n) for n in (*keep, *fill)][: self._n]

    def _unstopped(self, context: DecisionContext) -> list[str]:
        kept: list[str] = []
        for name in sorted(context.holdings):
            (last,) = context.view.history(name, 1)
            if self._stop.is_hit(context.holdings[name], last.close.amount):
                self.record.stops += 1
            else:
                kept.append(name)
        return kept

    def _ranking(self, context: DecisionContext) -> list[str]:
        needed = self._lookback + SKIP_SESSIONS + 1
        scored: list[tuple[Decimal, str]] = []
        for name in sorted(context.tradable):
            bars = context.view.history(name, needed)
            if len(bars) < needed:
                continue
            momentum = bars[-(SKIP_SESSIONS + 1)].close.amount / bars[0].close.amount - 1
            if momentum > 0:
                scored.append((momentum, name))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [name for _, name in scored]
