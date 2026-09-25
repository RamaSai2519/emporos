"""The §3.2 numbers for a swing arm, from its daily equity (EM-223).

Everything here is a function of a `SwingRun` and nothing else: net CAGR, net Sharpe, the monthly
and yearly return series, maximum drawdown, days in cash, round trips, and how concentrated the
profit is. These are statistics, not money: they use floats, once, at the end of the arithmetic.

Conventions, fixed here so every arm is measured the same way:
* A daily return is equity over the previous equity, the first day against the initial capital.
* Sharpe is the mean daily return over its sample standard deviation, times sqrt(252), with a zero
  risk-free rate (cash earns nothing in this simulation, so the comparison is between arms).
* CAGR compounds the total return over the calendar span between the first and last session.
* A month (or year) return is the equity at its last session over the equity at the previous
  month's (year's) last session, the first against the initial capital.
* The t-statistic of the monthly returns is mean / (sample sd / sqrt(months)).
* Concentration is the largest instrument's net profit over the total net profit; it is only
  defined when the total is positive, and only positive contributions count towards the largest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt
from typing import Literal

from emporos.research.swing.simulator import SwingRun

__all__ = ["SwingMetrics", "SwingStats", "max_drawdown", "period_returns", "sharpe"]

TRADING_DAYS = 252
Period = Literal["month", "year"]


@dataclass(frozen=True)
class SwingStats:
    net_cagr: float
    net_sharpe: float | None
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
    days_in_cash: int
    max_instrument_share: float | None
    net_profit: float


def sharpe(returns: Sequence[float]) -> float | None:
    """Annualised Sharpe of daily returns, or None when there is no spread to divide by."""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return None
    return mean / sqrt(variance) * sqrt(TRADING_DAYS)


def max_drawdown(equity: Sequence[Decimal], start: Decimal) -> float:
    peak, worst = start, Decimal(0)
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return float(worst)


def period_returns(
    days: Sequence[date], equity: Sequence[Decimal], start: Decimal, *, by: Period
) -> list[float]:
    """Calendar-month (`by="month"`) or calendar-year (`by="year"`) returns."""
    ends: dict[tuple[int, ...], Decimal] = {}
    for day, value in zip(days, equity, strict=True):
        key = (day.year, day.month) if by == "month" else (day.year,)
        ends[key] = value  # the last session of the period wins
    returns: list[float] = []
    previous = start
    for key in sorted(ends):
        returns.append(float(ends[key] / previous - 1))
        previous = ends[key]
    return returns


class SwingMetrics:
    @staticmethod
    def of(run: SwingRun) -> SwingStats:
        if not run.days:
            raise ValueError("a run with no sessions has no metrics")
        daily = SwingMetrics.daily_returns(run)
        months = period_returns(run.days, run.equity, run.capital, by="month")
        years = period_returns(run.days, run.equity, run.capital, by="year")
        total = float(run.equity[-1] / run.capital - 1)
        span_years = max((run.days[-1] - run.days[0]).days, 1) / 365.25
        cagr = (1 + total) ** (1 / span_years) - 1 if total > -1 else -1.0
        return SwingStats(
            net_cagr=cagr,
            net_sharpe=sharpe(daily),
            total_return=total,
            months=len(months),
            positive_month_share=sum(m > 0 for m in months) / len(months),
            worst_month=min(months),
            monthly_t=SwingMetrics._t_statistic(months),
            years=len(years),
            positive_year_share=sum(y > 0 for y in years) / len(years),
            max_drawdown=max_drawdown(run.equity, run.capital),
            round_trips=len(run.trades),
            days=len(run.days),
            days_in_cash=run.days_in_cash,
            max_instrument_share=SwingMetrics._concentration(run),
            net_profit=float(run.equity[-1] - run.capital),
        )

    @staticmethod
    def daily_returns(run: SwingRun) -> list[float]:
        previous = (run.capital, *run.equity[:-1])
        return [float(now / before - 1) for now, before in zip(run.equity, previous, strict=True)]

    @staticmethod
    def _t_statistic(values: Sequence[float]) -> float | None:
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        if variance <= 0:
            return None
        return mean / (sqrt(variance) / sqrt(len(values)))

    @staticmethod
    def _concentration(run: SwingRun) -> float | None:
        by_name = run.pnl_by_instrument
        total = sum(by_name.values(), Decimal(0))
        if total <= 0:
            return None
        return float(max(max(by_name.values()), Decimal(0)) / total)
