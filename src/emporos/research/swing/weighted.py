"""A fixed-weight buy-and-hold book, rebalanced on a calendar, net of the same costs (EM-233).

A5's benchmark (60/40 NIFTYBEES/GOLDBEES, rebalanced yearly) and A4's ETF-sleeve benchmark. It holds
whole units, pays the dated delivery schedule and the scenario's slippage on every trade, and
rebalances by trading only the DIFFERENCE from the target weights (sell the over-weight first, then
buy the under-weight with the cash there is): a full sell-and-rebuy every year would overstate its
costs and flatter whatever it is compared with.

Timing is the strategies': the rebalance is decided at the close of the calendar's first session
and traded at the next open; the first purchase is decided at the first close and made at the
second session's open. Everything held is sold at the last close so the final equity is net. Cash
(the remainder of whole units) accrues `cash_yield` like everyone's.

`TargetWeightBook` is the general engine: what to hold is asked of a `TargetWeights` policy at
each rebalance close (and, if `decide_at_start`, at the first close), and the fill is at the next
session's open or, for a core ETF cell (PROFIT_PLAN §10), its close. `WeightedBuyHold` is the
fixed-weight case.

Holdings are counted in ANALYSIS units, so a unit split (a flattened artifact) does not change the
value of a holding; a sale is in whole raw units of the day's basis.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from math import floor
from typing import Protocol

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.swing.costs import SwingCostModel
from emporos.research.swing.data import AsOfView, SwingDataset
from emporos.research.swing.regime import RebalanceCalendar
from emporos.research.swing.simulator import FillPrice, SwingRun

__all__ = ["FixedWeights", "TargetWeightBook", "TargetWeights", "WeightedBuyHold"]


class TargetWeights(Protocol):
    def targets(self, view: AsOfView) -> Mapping[str, Decimal] | None:
        """The book's target weights from the next fill, decided with data up to `view.day` only:
        names not listed are sold, weights adding to less than 1 leave the rest in cash. None means
        no decision (the book is left as it is)."""
        ...


class FixedWeights:
    def __init__(self, weights: Mapping[str, Decimal]) -> None:
        if not weights or any(w <= 0 for w in weights.values()):
            raise ValueError("every weight is positive")
        if sum(weights.values(), Decimal(0)) > 1:
            raise ValueError("the weights add up to at most 1 (the rest is cash)")
        self._weights = dict(weights)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._weights)

    def targets(self, view: AsOfView) -> Mapping[str, Decimal] | None:
        return self._weights


class TargetWeightBook:
    def __init__(
        self,
        dataset: SwingDataset,
        policy: TargetWeights,
        assets: Sequence[str],
        costs: SwingCostModel,
        rebalance: RebalanceCalendar,
        capital: Decimal,
        cash_yield: Decimal = Decimal(0),
        start_day: date | None = None,
        fill: FillPrice = FillPrice.OPEN,
        decide_at_start: bool = True,
    ) -> None:
        if not assets:
            raise ValueError("a book holds at least one asset")
        missing = set(assets) - set(dataset.instrument_ids)
        if missing:
            raise ValueError(f"no bars for {sorted(missing)}")
        if capital <= 0 or cash_yield < 0:
            raise ValueError("capital is positive and a cash yield is not negative")
        self._data = dataset
        self._policy = policy
        self._assets = tuple(dict.fromkeys(assets))  # in the order given: buys share the cash in it
        self._costs = costs
        self._calendar = rebalance
        self._capital = capital
        self._growth = (1 + cash_yield) ** (Decimal(1) / 252)
        self._start = start_day
        self._field = fill.value
        self._decide_at_start = decide_at_start

    def run(self) -> SwingRun:
        calendar = self._data.calendar
        first = 0
        if self._start is not None:
            found = next((i for i, d in enumerate(calendar) if d >= self._start), None)
            if found is None:
                raise ValueError(f"the data has no session on or after {self._start}")
            first = found
        days = calendar[first:]
        cash = self._capital
        units: dict[str, Decimal] = {n: Decimal(0) for n in self._assets}  # analysis units
        fees_paid = Decimal(0)
        equity: list[Decimal] = []
        flags: list[bool] = []
        due = self._decide_at_start  # the first purchase is decided at the first close
        pending: Mapping[str, Decimal] | None = None
        last = len(days) - 1
        for i, day in enumerate(days):
            if i > 0:
                cash *= self._growth
            if pending is not None:
                cash, spent = self._rebalance(day, units, cash, pending)
                fees_paid += spent
                pending = None
            if i == last:
                cash, spent = self._liquidate(day, units, cash)
                fees_paid += spent
            value = cash + sum(
                (units[n] * self._analysis(n, day, "close") for n in units), Decimal(0)
            )
            equity.append(value)
            flags.append(any(u > 0 for u in units.values()))
            if i < last:
                view = AsOfView(self._data, day)
                if due or self._calendar.is_rebalance(view):
                    pending, due = self._policy.targets(view), False
        return SwingRun(
            days, tuple(equity), sum(flags), (), self._capital, fees_paid, 0, tuple(flags)
        )

    # -- helpers --------------------------------------------------------------------------------

    def _raw(self, bar: Candle) -> Decimal:
        return bar.open.amount if self._field == "open" else bar.close.amount

    def _bar(self, name: str, day: date) -> int | None:
        return self._data.bar_on(name, day)

    def _analysis(self, name: str, day: date, field: str) -> Decimal:
        """The name's analysis price on `day` (its last bar's close if it did not trade)."""
        series = self._data.series(name)
        index = series.last_index_on_or_before(day)
        if index is None:
            return Decimal(0)
        bar = series.analysis[index]
        on_day = series.days[index] == day
        return bar.open.amount if field == "open" and on_day else bar.close.amount

    def _rebalance(
        self, day: date, units: dict[str, Decimal], cash: Decimal, weights: Mapping[str, Decimal]
    ) -> tuple[Decimal, Decimal]:
        fees = Decimal(0)
        field = self._field
        tradable = {n: self._bar(n, day) for n in self._assets}
        targets = {n: weights.get(n, Decimal(0)) for n in self._assets}
        value = cash + sum((units[n] * self._analysis(n, day, field) for n in units), Decimal(0))
        for name, weight in targets.items():  # sells first
            index = tradable[name]
            if index is None:
                continue
            series = self._data.series(name)
            m = series.multipliers[index]
            excess = units[name] * self._analysis(name, day, field) - weight * value
            price = self._costs.sell_price(self._raw(series.raw[index]))
            quantity = min(floor(excess / price), floor(units[name] * m)) if excess > 0 else 0
            if quantity >= 1:
                fee = self._costs.fees(OrderSide.SELL, quantity, price)
                cash += quantity * price - fee
                fees += fee
                units[name] -= Decimal(quantity) / m
        for name, weight in targets.items():  # then buys
            index = tradable[name]
            if index is None:
                continue
            series = self._data.series(name)
            m = series.multipliers[index]
            deficit = min(weight * value - units[name] * self._analysis(name, day, field), cash)
            price = self._costs.buy_price(self._raw(series.raw[index]))
            quantity = floor(deficit / price) if deficit > 0 else 0
            while quantity >= 1:
                fee = self._costs.fees(OrderSide.BUY, quantity, price)
                if quantity * price + fee <= cash:
                    cash -= quantity * price + fee
                    fees += fee
                    units[name] += Decimal(quantity) / m
                    break
                quantity -= 1
        return cash, fees

    def _liquidate(
        self, day: date, units: dict[str, Decimal], cash: Decimal
    ) -> tuple[Decimal, Decimal]:
        fees = Decimal(0)
        for name in self._assets:
            if units[name] <= 0:
                continue
            series = self._data.series(name)
            index = series.last_index_on_or_before(day)
            assert index is not None
            price = self._costs.sell_price(series.raw[index].close.amount)
            m = series.multipliers[index]
            quantity = max(1, int((units[name] * m).to_integral_value()))
            fee = self._costs.fees(OrderSide.SELL, quantity, price)
            cash += units[name] * m * price - fee
            fees += fee
            units[name] = Decimal(0)
        return cash, fees


class WeightedBuyHold(TargetWeightBook):
    """Fixed target weights, restored at every calendar rebalance (the first purchase at the first
    close)."""

    def __init__(
        self,
        dataset: SwingDataset,
        weights: Mapping[str, Decimal],
        costs: SwingCostModel,
        rebalance: RebalanceCalendar,
        capital: Decimal,
        cash_yield: Decimal = Decimal(0),
        start_day: date | None = None,
        fill: FillPrice = FillPrice.OPEN,
    ) -> None:
        fixed = FixedWeights(weights)
        super().__init__(
            dataset, fixed, fixed.names, costs, rebalance, capital, cash_yield, start_day, fill
        )
