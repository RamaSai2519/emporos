"""A5: monthly dual-momentum rotation among broad ETFs (a5-etf-dual-momentum.yaml, EM-233).

Every rule is a line of the declaration; nothing is added and nothing is tuned.

* Decisions at the close of the first session of each calendar month, fills at the next open.
* Score per asset, on analysis closes: `12-1` = close[t-21] / close[t-252] - 1; `blend` = the mean
  of the 3-, 6- and 12-month returns (63, 126 and 252 sessions, each ending at t).
* An asset qualifies if its close is above its 200-session simple average AND its score is above
  0 (absolute momentum). The book holds the top K qualifying assets by score (ties by id), one
  slot of equity / K each; fewer than K qualifying leaves the rest in cash (which accrues the
  declared yield).
* Nothing is decided until EVERY asset has 253 sessions of history, so the effective start is the
  first month-start on which all of them do; before that the book is in cash.
* Between months the book is left alone. No stop, no book rules (reported, not applied).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.research.swing.regime import RebalanceCalendar
from emporos.research.swing.rules import DecisionContext, Intent

__all__ = ["EtfDualMomentum", "RotationRecord", "SCORES"]

SCORES = ("12-1", "blend")
HISTORY = 253  # 252 sessions back from today needs 253 closes
SMA_WINDOW = 200


@dataclass
class RotationRecord:
    first_ranked_day: date | None = None
    rebalances: int = 0
    months_all_cash: int = 0
    months_held: dict[str, int] = field(default_factory=dict)


class EtfDualMomentum:
    def __init__(
        self, score: str, top_k: int, assets: Sequence[str], calendar: RebalanceCalendar
    ) -> None:
        if score not in SCORES:
            raise ValueError(f"the score is one of {SCORES}")
        if top_k < 1 or not assets:
            raise ValueError("the book holds at least one of at least one asset")
        self._score = score
        self._k = top_k
        self._assets = tuple(sorted(assets))
        self._calendar = calendar
        self.record = RotationRecord()

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        if not self._calendar.is_rebalance(context.view):
            return [Intent(n) for n in sorted(context.holdings)]
        histories = {n: context.view.history(n, HISTORY) for n in self._assets}
        if any(len(h) < HISTORY for h in histories.values()):
            return []  # not every asset has its window yet: the book waits in cash
        if self.record.first_ranked_day is None:
            self.record.first_ranked_day = context.day
        self.record.rebalances += 1
        ranked = sorted(
            (
                (self._scored(h), n)
                for n, h in histories.items()
                if self._qualifies(h) and n in context.tradable
            ),
            key=lambda pair: (-pair[0], pair[1]),
        )
        chosen = [n for _, n in ranked[: self._k]]
        if not chosen:
            self.record.months_all_cash += 1
        for name in chosen:
            self.record.months_held[name] = self.record.months_held.get(name, 0) + 1
        return [Intent(n) for n in chosen]

    def _qualifies(self, closes: Sequence[Candle]) -> bool:
        last = closes[-1].close.amount
        average = sum((c.close.amount for c in closes[-SMA_WINDOW:]), Decimal(0)) / SMA_WINDOW
        return last > average and self._scored(closes) > 0

    def _scored(self, closes: Sequence[Candle]) -> Decimal:
        c = [bar.close.amount for bar in closes]
        if self._score == "12-1":
            return c[-22] / c[-253] - 1
        return sum((c[-1] / c[-1 - back] - 1 for back in (63, 126, 252)), Decimal(0)) / 3
