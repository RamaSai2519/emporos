"""A book of sleeves: separately simulated runs, combined at a fixed split (EM-233, cell A4).

Each sleeve is a `SwingRun` simulated on its own capital by the ordinary simulator. The combiner
holds each sleeve at its target share of the book's equity and, on the first common session of each
calendar quarter, restores that split by moving the difference between the sleeves. Between resets
a sleeve's value moves with its own run's day-to-day return.

This is the SLEEVE-LEVEL approximation, stated once here and in every report that uses it:

* a sleeve's run is simulated at its INITIAL capital and only its return series is carried into the
  book, so after a reset the sleeve's whole-share rounding, cash drag and position sizes are those
  of the run, not of the new capital;
* the transfer is made at the previous close of the reset session and costs the round-trip rate of a
  reference order (`TransferCost`), not the fees of the actual trims, and the trims are not rounded
  to whole shares;
* a rule inside a sleeve (the §3.5 halt and kill) sees the sleeve's own P&L, never the book's.

The book's trades are the sleeves' trades with the rupee fields scaled by the sleeve's value in the
book over its value in its own run on the exit day (`quantity` and prices stay the run's), which
puts every trade's P&L in the book's rupees for the concentration figures.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from math import sqrt

from emporos.domain.orders import OrderSide
from emporos.research.swing.costs import SwingCostModel
from emporos.research.swing.rules import DecisionContext, Intent, SwingStrategy
from emporos.research.swing.simulator import SwingRun, Trade

__all__ = [
    "CombinedBook", "MonthlyCorrelation", "SleeveBook", "SleeveCombiner", "SleeveRun", "Transfer",
    "TransferCost", "pearson", "quarter_of",
]  # fmt: skip

REFERENCE_ORDER = Decimal(25_000)
_REFERENCE_PRICE = Decimal(100)


@dataclass(frozen=True)
class TransferCost:
    """The cost per rupee moved from one sleeve to the other: the round trip (sell one side, buy the
    other) of a reference order, fees and slippage as the scenario has them."""

    rate: Decimal

    def __post_init__(self) -> None:
        if self.rate < 0:
            raise ValueError("a transfer cost is not negative")

    @staticmethod
    def of(costs: SwingCostModel, reference_order: Decimal = REFERENCE_ORDER) -> TransferCost:
        quantity = int(reference_order / _REFERENCE_PRICE)
        if quantity < 1:
            raise ValueError("the reference order buys no shares")
        buy, sell = costs.buy_price(_REFERENCE_PRICE), costs.sell_price(_REFERENCE_PRICE)
        paid = quantity * buy + costs.fees(OrderSide.BUY, quantity, buy)
        received = quantity * sell - costs.fees(OrderSide.SELL, quantity, sell)
        return TransferCost((paid - received) / (quantity * _REFERENCE_PRICE))


@dataclass(frozen=True)
class SleeveRun:
    name: str
    run: SwingRun


@dataclass(frozen=True)
class Transfer:
    """One quarterly reset: the rupees moved and what moving them cost."""

    day: date
    moved: Decimal
    cost: Decimal


@dataclass(frozen=True)
class CombinedBook:
    run: SwingRun  # the book, capital the sum of the sleeves' shares of the book's capital
    transfers: tuple[Transfer, ...]
    sleeve_days_dropped: int  # sessions a sleeve had that the other did not (not in the book)

    @property
    def transfer_cost(self) -> Decimal:
        return sum((t.cost for t in self.transfers), Decimal(0))


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3


class SleeveBook:
    """The `strategy` a combined book reports: it decides nothing itself, its sleeves already ran.
    It keeps the sleeves' strategy instances so their own records (halts, kills, months held) can
    be read for the report."""

    def __init__(self, sleeves: Mapping[str, SwingStrategy]) -> None:
        self.sleeves = dict(sleeves)

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        return ()


class SleeveCombiner:
    def __init__(
        self, weights: Sequence[Decimal], transfer: TransferCost, capital: Decimal
    ) -> None:
        if not weights or any(w <= 0 for w in weights) or sum(weights, Decimal(0)) != 1:
            raise ValueError("the sleeves' weights are positive and add up to 1")
        if capital <= 0:
            raise ValueError("capital is positive")
        self._weights = tuple(weights)
        self._transfer = transfer
        self._capital = capital

    def combine(self, sleeves: Sequence[SleeveRun]) -> CombinedBook:
        if len(sleeves) != len(self._weights):
            raise ValueError("one weight per sleeve")
        days = self._common_days(sleeves)
        if not days:
            raise ValueError("the sleeves have no session in common")
        index = [{d: i for i, d in enumerate(s.run.days)} for s in sleeves]
        at = [[index[k][d] for d in days] for k in range(len(sleeves))]
        values = [
            w * self._capital * s.run.equity[at[k][0]] / s.run.capital
            for k, (w, s) in enumerate(zip(self._weights, sleeves, strict=True))
        ]
        equity = [sum(values, Decimal(0))]
        scales = [[self._scale(values[k], sleeves[k], at[k][0]) for k in range(len(sleeves))]]
        transfers: list[Transfer] = []
        for t in range(1, len(days)):
            if quarter_of(days[t]) != quarter_of(days[t - 1]):
                values, transfer = self._reset(days[t], values)
                transfers.append(transfer)
            values = [
                v * s.run.equity[at[k][t]] / s.run.equity[at[k][t - 1]]
                for k, (v, s) in enumerate(zip(values, sleeves, strict=True))
            ]
            equity.append(sum(values, Decimal(0)))
            scales.append(
                [self._scale(values[k], sleeves[k], at[k][t]) for k in range(len(sleeves))]
            )
        flags = tuple(
            any(bool(s.run.invested_flags[at[k][t]]) for k, s in enumerate(sleeves))
            for t in range(len(days))
        )
        run = SwingRun(
            tuple(days), tuple(equity), sum(flags), self._trades(sleeves, days, scales),
            self._capital, self._fees(sleeves, days, scales), 0, flags,
        )  # fmt: skip
        dropped = sum(len(s.run.days) - len(days) for s in sleeves)
        return CombinedBook(run, tuple(transfers), dropped)

    # -- helpers --------------------------------------------------------------------------------

    @staticmethod
    def _common_days(sleeves: Sequence[SleeveRun]) -> list[date]:
        common = set(sleeves[0].run.days)
        for sleeve in sleeves[1:]:
            common &= set(sleeve.run.days)
        return sorted(common)

    def _reset(self, day: date, values: list[Decimal]) -> tuple[list[Decimal], Transfer]:
        """Restore the split at the previous close: the rupees the over-weight sleeves give up."""
        total = sum(values, Decimal(0))
        moved = sum(
            (max(v - w * total, Decimal(0)) for v, w in zip(values, self._weights, strict=False)),
            Decimal(0),
        )
        cost = moved * self._transfer.rate
        after = total - cost
        return [w * after for w in self._weights], Transfer(day, moved, cost)

    @staticmethod
    def _scale(value: Decimal, sleeve: SleeveRun, at: int) -> Decimal:
        own = sleeve.run.equity[at]
        return value / own if own > 0 else Decimal(1)

    def _trades(
        self, sleeves: Sequence[SleeveRun], days: list[date], scales: list[list[Decimal]]
    ) -> tuple[Trade, ...]:
        out: list[Trade] = []
        for k, sleeve in enumerate(sleeves):
            for trade in sleeve.run.trades:
                factor = self._factor(trade.exit_day, days, scales, k)
                out.append(replace(trade, fees=trade.fees * factor, net_pnl=trade.net_pnl * factor))
        return tuple(sorted(out, key=lambda t: (t.exit_day, t.instrument_id)))

    def _fees(
        self, sleeves: Sequence[SleeveRun], days: list[date], scales: list[list[Decimal]]
    ) -> Decimal:
        total = Decimal(0)
        for k, sleeve in enumerate(sleeves):
            for trade in sleeve.run.trades:
                total += trade.fees * self._factor(trade.exit_day, days, scales, k)
        return total

    @staticmethod
    def _factor(day: date, days: list[date], scales: list[list[Decimal]], k: int) -> Decimal:
        """The sleeve's scale on the last common session on or before `day`."""
        lo, hi = 0, len(days) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if days[mid] <= day:
                lo = mid
            else:
                hi = mid - 1
        return scales[lo][k]


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Correlation of two equal-length series, or None when either is flat (or under 2 long)."""
    if len(xs) != len(ys):
        raise ValueError("the series are the same length")
    if len(xs) < 2:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sqrt(sxx * syy)


@dataclass(frozen=True)
class MonthlyCorrelation:
    """Two sleeves' monthly returns: the correlation over every month they share, and over the first
    sleeve's worst `worst_n` months (the months a diversifier has to help in)."""

    months: int
    overall: float | None
    worst_months: tuple[tuple[int, int], ...]
    in_worst: float | None
    first_mean_in_worst: float
    second_mean_in_worst: float

    @staticmethod
    def of(
        first: dict[tuple[int, int], float], second: dict[tuple[int, int], float], worst_n: int = 12
    ) -> MonthlyCorrelation:
        shared = sorted(set(first) & set(second))
        if not shared:
            raise ValueError("the sleeves share no month")
        worst = tuple(sorted(sorted(shared, key=lambda m: first[m])[:worst_n]))
        a, b = [first[m] for m in worst], [second[m] for m in worst]
        return MonthlyCorrelation(
            len(shared),
            pearson([first[m] for m in shared], [second[m] for m in shared]),
            worst,
            pearson(a, b),
            sum(a) / len(a),
            sum(b) / len(b),
        )
