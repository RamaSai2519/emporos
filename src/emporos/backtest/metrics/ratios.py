"""Return-based ratios: total return, CAGR, Sharpe, Sortino (plan.md §10).

Conventions, all deliberate and all stated in the report:

* Returns are DAILY (see `equity.py`); annualised with `sqrt(annualisation_days)`, 252 by default.
* The risk-free rate is an annual figure, converted to a daily one by dividing by the same 252.
  It is 0 unless configured.
* Sharpe = mean(daily return - daily rf) / sample stdev(daily return) * sqrt(252).
* Sortino = (mean(daily return) - daily rf) / downside deviation * sqrt(252), where the downside
  deviation is sqrt(sum(min(r - daily rf, 0)^2) / n) over ALL n days (the target semideviation).
* CAGR = (end equity / starting cash) ** (1 / years) - 1 with years = calendar days from the first
  to the last trading day INCLUSIVE, divided by 365. Over less than a year it extrapolates.
* A ratio whose denominator is zero (no variance, no losing day) is None, never infinity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ONE, ZERO, DecimalMath
from emporos.backtest.metrics.equity import DailyEquity

DAYS_PER_YEAR = Decimal(365)


@dataclass(frozen=True)
class ReturnRatios:
    total_return: Decimal
    cagr: Decimal | None
    years: Decimal
    sharpe: Decimal | None
    sortino: Decimal | None
    daily_returns: int


class RatioCalculator:
    def __init__(self, annualisation_days: int = 252, risk_free_annual: Decimal = ZERO) -> None:
        if annualisation_days <= 0:
            raise ValueError("annualisation days must be positive")
        self._days = annualisation_days
        self._root = DecimalMath.sqrt(Decimal(annualisation_days))
        self._daily_rf = DecimalMath.divide(risk_free_annual, Decimal(annualisation_days))

    def calculate(self, starting_cash: Decimal, days: Sequence[DailyEquity]) -> ReturnRatios:
        if not days:
            return ReturnRatios(ZERO, None, ZERO, None, None, 0)
        end = days[-1].equity
        years = self._years(days[0].day, days[-1].day)
        returns = [d.ret for d in days]
        return ReturnRatios(
            total_return=DecimalMath.divide(end, starting_cash) - ONE,
            cagr=self._cagr(starting_cash, end, years),
            years=years,
            sharpe=self._sharpe(returns),
            sortino=self._sortino(returns),
            daily_returns=len(returns),
        )

    @staticmethod
    def _years(first: date, last: date) -> Decimal:
        return DecimalMath.divide(Decimal((last - first).days + 1), DAYS_PER_YEAR)

    @staticmethod
    def _cagr(start: Decimal, end: Decimal, years: Decimal) -> Decimal | None:
        if end < ZERO:
            return None  # the account went negative: a growth rate is meaningless
        if end == ZERO:
            return -ONE
        return (
            DecimalMath.power(DecimalMath.divide(end, start), DecimalMath.divide(ONE, years)) - ONE
        )

    def _sharpe(self, returns: Sequence[Decimal]) -> Decimal | None:
        if len(returns) < 2:
            return None
        spread = DecimalMath.sample_stdev(returns)
        if spread == ZERO:
            return None
        excess = DecimalMath.mean(returns) - self._daily_rf
        return DecimalMath.divide(excess, spread) * self._root

    def _sortino(self, returns: Sequence[Decimal]) -> Decimal | None:
        shortfalls = [min(r - self._daily_rf, ZERO) ** 2 for r in returns]
        deviation = DecimalMath.sqrt(DecimalMath.mean(shortfalls)) if returns else ZERO
        if deviation == ZERO:
            return None
        excess = DecimalMath.mean(returns) - self._daily_rf
        return DecimalMath.divide(excess, deviation) * self._root
