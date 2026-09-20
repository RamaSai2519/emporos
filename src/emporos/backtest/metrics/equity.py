"""Turning the equity curve into daily equity and daily returns.

A day is an Indian trading day (IST date). The day's equity is the LAST point recorded on it. The
first day's return is measured from the starting cash; a day the strategy sat out contributes a
return of exactly zero, as it would in a real account."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ONE, DecimalMath
from emporos.backtest.portfolio import EquityPoint
from emporos.core.clock import IST


@dataclass(frozen=True)
class DailyEquity:
    day: date
    equity: Decimal
    ret: Decimal  # this day's equity over the previous day's (or the starting cash), minus one


class DailySeries:
    def build(
        self, starting_cash: Decimal, curve: Sequence[EquityPoint]
    ) -> tuple[DailyEquity, ...]:
        last_of_day: dict[date, Decimal] = {}
        for point in curve:  # the curve is in time order, so later points overwrite earlier ones
            last_of_day[point.ts.astimezone(IST).date()] = point.equity.amount
        previous = starting_cash
        days: list[DailyEquity] = []
        for day in sorted(last_of_day):
            equity = last_of_day[day]
            days.append(DailyEquity(day, equity, DecimalMath.divide(equity, previous) - ONE))
            previous = equity
        return tuple(days)
