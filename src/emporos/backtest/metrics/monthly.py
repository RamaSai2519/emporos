"""The monthly return table: each calendar month's change in equity, month-end to month-end.
The first month is measured from the starting cash. Months are Indian (IST) calendar months."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ONE, DecimalMath
from emporos.backtest.metrics.equity import DailyEquity
from emporos.domain.money import Money


@dataclass(frozen=True)
class MonthlyReturn:
    month: str  # "2026-01"
    ret: Decimal
    pnl: Money  # the month's change in equity, after charges


class MonthlyTable:
    def build(
        self, starting_cash: Decimal, days: Sequence[DailyEquity]
    ) -> tuple[MonthlyReturn, ...]:
        month_end: dict[str, Decimal] = {}
        for day in days:  # in date order, so the last day of each month wins
            month_end[f"{day.day.year:04d}-{day.day.month:02d}"] = day.equity
        previous = starting_cash
        rows: list[MonthlyReturn] = []
        for month in sorted(month_end):
            equity = month_end[month]
            rows.append(
                MonthlyReturn(
                    month, DecimalMath.divide(equity, previous) - ONE, Money(equity - previous)
                )
            )
            previous = equity
        return tuple(rows)
