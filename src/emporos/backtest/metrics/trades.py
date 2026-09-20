"""Statistics over closed round trips (plan.md §10).

* A WIN has net P&L (after charges) above zero, a LOSS below, otherwise BREAKEVEN.
* profit factor = gross profit / gross loss (net P&L per trade, summed by sign); None when there
  is no losing trade to divide by.
* average trade = mean net P&L per trade (money). expectancy = mean of net P&L / the notional
  entered, per trade: the average return a trade earned on the money it put to work.
* Streaks are runs of consecutive trades, in the order they closed; a breakeven trade breaks both.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.portfolio import ClosedTrade
from emporos.domain.money import Money


@dataclass(frozen=True)
class TradeStatistics:
    count: int
    wins: int
    losses: int
    breakeven: int
    win_rate: Decimal | None
    gross_pnl: Money
    fees: Money
    net_pnl: Money
    gross_profit: Money
    gross_loss: Money  # a positive magnitude
    profit_factor: Decimal | None
    average_trade: Money | None
    average_win: Money | None
    average_loss: Money | None  # negative
    expectancy: Decimal | None
    max_consecutive_wins: int
    max_consecutive_losses: int


class TradeAnalyzer:
    def analyze(self, trades: Sequence[ClosedTrade]) -> TradeStatistics:
        ordered = sorted(trades, key=lambda t: (t.closed_at, t.opened_at, t.instrument_id))
        nets = [t.net_pnl.amount for t in ordered]
        wins = [n for n in nets if n > ZERO]
        losses = [n for n in nets if n < ZERO]
        profit, loss = sum(wins, ZERO), -sum(losses, ZERO)
        count = len(ordered)
        return TradeStatistics(
            count=count,
            wins=len(wins),
            losses=len(losses),
            breakeven=count - len(wins) - len(losses),
            win_rate=self._ratio(Decimal(len(wins)), count),
            gross_pnl=Money(sum((t.gross_pnl.amount for t in ordered), ZERO)),
            fees=Money(sum((t.fees.amount for t in ordered), ZERO)),
            net_pnl=Money(sum(nets, ZERO)),
            gross_profit=Money(profit),
            gross_loss=Money(loss),
            profit_factor=None if loss == ZERO else DecimalMath.divide(profit, loss),
            average_trade=self._money_mean(nets),
            average_win=self._money_mean(wins),
            average_loss=self._money_mean(losses),
            expectancy=self._expectancy(ordered),
            max_consecutive_wins=self._longest(nets, lambda n: n > ZERO),
            max_consecutive_losses=self._longest(nets, lambda n: n < ZERO),
        )

    @staticmethod
    def _ratio(part: Decimal, whole: int) -> Decimal | None:
        return None if whole == 0 else DecimalMath.divide(part, Decimal(whole))

    @staticmethod
    def _money_mean(values: Sequence[Decimal]) -> Money | None:
        return Money(DecimalMath.mean(values)) if values else None

    @staticmethod
    def _expectancy(trades: Sequence[ClosedTrade]) -> Decimal | None:
        if not trades:
            return None
        return DecimalMath.mean(
            [DecimalMath.divide(t.net_pnl.amount, t.entry_notional.amount) for t in trades]
        )

    @staticmethod
    def _longest(nets: Sequence[Decimal], counts: Callable[[Decimal], bool]) -> int:
        best = run = 0
        for net in nets:
            run = run + 1 if counts(net) else 0
            best = max(best, run)
        return best
