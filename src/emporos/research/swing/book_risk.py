"""Book-level risk rules and volatility targeting around a swing strategy (A1b, EM-232).

Both wrap an inner `SwingStrategy` and change only what it is allowed to buy or hold; neither knows
which strategy it wraps, and the stop and every ranking rule stay inside the inner one.

`BookRiskRules` (PROFIT_PLAN §3.5, declared before any Track A result):
* MONTH HALT: when book equity at a close is 6% or more below the equity at the close before the
  month's first session, no new entries are decided from that close to the end of the calendar
  month. Holdings the inner strategy still wants are kept; its stops stay active.
* KILL: when book equity at a close is 15% or more below its running peak, the book wants nothing,
  so every holding is sold at the next open. It re-enters only on the first rebalance session of a
  LATER calendar month on which the regime filter is on; the running peak is then reset to the
  equity at that re-entry.
The wrapper records every halt, kill and re-entry date for the report.

`VolTargeted` scales the slot of every NEW position by `min(1, target / sigma)`, where sigma is the
annualised (x sqrt(252)) sample standard deviation of the index's daily close-to-close returns over
the previous `window` sessions, known at the decision close. A held position is never resized;
what the targeting does not use stays in cash. No leverage: the multiple is never above 1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise
from math import sqrt

from emporos.research.swing.regime import IndexSeries, RebalanceCalendar
from emporos.research.swing.rules import DecisionContext, Intent, RegimeFilter, SwingStrategy

__all__ = ["BookRiskRecord", "BookRiskRules", "VolTargeted"]

TRADING_DAYS = 252


@dataclass
class BookRiskRecord:
    halts: list[date] = field(default_factory=list)
    kills: list[date] = field(default_factory=list)
    reentries: list[date] = field(default_factory=list)


class BookRiskRules:
    def __init__(
        self,
        inner: SwingStrategy,
        calendar: RebalanceCalendar,
        regime: RegimeFilter,
        month_halt: Decimal = Decimal("0.06"),
        kill: Decimal = Decimal("0.15"),
    ) -> None:
        if not (Decimal(0) < month_halt < Decimal(1) and Decimal(0) < kill < Decimal(1)):
            raise ValueError("the halt and the kill are fractions between 0 and 1")
        self.inner = inner
        self._calendar = calendar
        self._regime = regime
        self._halt = month_halt
        self._kill = kill
        self.record = BookRiskRecord()
        self._month: tuple[int, int] | None = None
        self._month_open = Decimal(0)
        self._last_equity: Decimal | None = None
        self._peak = Decimal(0)
        self._halted = False
        self._killed_in: tuple[int, int] | None = None

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        equity, month = context.equity, (context.day.year, context.day.month)
        if month != self._month:
            self._month = month
            self._month_open = self._last_equity if self._last_equity is not None else equity
            self._halted = False
        self._last_equity = equity
        if self._killed_in is not None:
            if not self._may_reenter(context, month):
                return []
            self._killed_in = None
            self._peak = equity
            self.record.reentries.append(context.day)
        else:
            self._peak = max(self._peak, equity)
            if equity <= self._peak * (1 - self._kill):
                self._killed_in = month
                self.record.kills.append(context.day)
                return []
        if not self._halted and equity <= self._month_open * (1 - self._halt):
            self._halted = True
            self.record.halts.append(context.day)
        intents = self.inner.desired(context)
        if self._halted:
            return [i for i in intents if i.instrument_id in context.holdings]
        return intents

    def _may_reenter(self, context: DecisionContext, month: tuple[int, int]) -> bool:
        return (
            self._killed_in is not None
            and month > self._killed_in
            and self._calendar.is_rebalance(context.view)
            and self._regime.is_on(context)
        )


class VolTargeted:
    def __init__(
        self,
        inner: SwingStrategy,
        index: IndexSeries,
        target: Decimal = Decimal("0.15"),
        window: int = 20,
    ) -> None:
        if target <= 0 or window < 2:
            raise ValueError("the volatility target is positive and the window has 2+ returns")
        self.inner = inner
        self._index = index
        self._target = target
        self._window = window

    def exposure(self, day: date) -> Decimal:
        """`min(1, target / sigma)` at the close of `day`; 1 while sigma is unknown or zero."""
        closes = self._index.closes_up_to(day, self._window + 1)
        if len(closes) < self._window + 1:
            return Decimal(1)
        returns = [float(b / a - 1) for a, b in pairwise(closes)]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        sigma = sqrt(variance) * sqrt(TRADING_DAYS)
        if sigma <= 0:
            return Decimal(1)
        return min(Decimal(1), self._target / Decimal(str(sigma)))

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        intents = self.inner.desired(context)
        exposure = self.exposure(context.day)
        return [
            i if i.instrument_id in context.holdings else Intent(i.instrument_id, exposure)
            for i in intents
        ]
