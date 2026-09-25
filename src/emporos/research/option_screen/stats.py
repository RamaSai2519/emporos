"""The §3.2 numbers for an option arm, from its daily equity (EM-230).

Conventions are Track A's, on purpose (`research.swing.metrics`), so arms of the two tracks read on
one scale: a month's return is the equity at its last session over the previous month's, CAGR
compounds the total return over the calendar span between the first and last session, and the
monthly t-statistic is mean / (sample sd / sqrt(months)). These are statistics, not money: floats,
once, at the end."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

from emporos.options.backtest import BacktestResult
from emporos.research.swing.metrics import max_drawdown, period_returns

__all__ = ["OptionStats", "monthly_t"]


@dataclass(frozen=True)
class OptionStats:
    net_cagr: float
    total_return: float
    months: int
    positive_month_share: float
    worst_month: float
    monthly_t: float | None
    years: int
    positive_year_share: float
    max_drawdown: float  # a positive fraction: 0.2 is a 20% peak-to-trough fall
    round_trips: int
    days: int
    days_with_a_spread: int
    net_pnl: float

    @classmethod
    def of(cls, result: BacktestResult, capital: Decimal) -> OptionStats:
        if not result.days:
            raise ValueError("a run with no sessions has no metrics")
        days = [d.day for d in result.days]
        equity = [d.equity for d in result.days]
        months = period_returns(days, equity, capital, by="month")
        years = period_returns(days, equity, capital, by="year")
        total = float(equity[-1] / capital - 1)
        span_years = max((days[-1] - days[0]).days, 1) / 365.25
        cagr = (1 + total) ** (1 / span_years) - 1 if total > -1 else -1.0
        return cls(
            net_cagr=cagr,
            total_return=total,
            months=len(months),
            positive_month_share=sum(m > 0 for m in months) / len(months),
            worst_month=min(months),
            monthly_t=monthly_t(months),
            years=len(years),
            positive_year_share=sum(y > 0 for y in years) / len(years),
            max_drawdown=max_drawdown(equity, capital),
            round_trips=len(result.trades),
            days=len(days),
            days_with_a_spread=sum(1 for d in result.days if d.open_positions > 0),
            net_pnl=float(equity[-1] - capital),
        )


def monthly_t(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    if variance <= 0:
        return None
    return mean / (sqrt(variance) / sqrt(len(values)))
