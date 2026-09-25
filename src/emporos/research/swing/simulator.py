"""The swing portfolio simulation: daily bars, whole shares, delivery costs (EM-223).

One pass over the calendar. On each session:

1. **Fill** the orders decided at the previous close, at THIS session's raw open (or raw close, if
   the config says `FillPrice.CLOSE`): sells first (a
   name with no bar today waits for the next session), then buys in the strategy's order. A buy
   takes `equity x size_multiple / max_positions` of cash (never more than the cash there is), in
   whole shares at the slipped price, fees included. A name whose whole-share price does not fit is
   skipped and counted. Held names the strategy still wants are left as they are.
2. **Mark** every holding at its close on the analysis basis; the session's equity is cash plus
   those values. A name with no bar today keeps its last close.
3. **Decide** (except on the last session): ask the strategy, with data up to this close only, what
   it wants from the next open.

On the last session everything still held is sold at the close, with costs, so the final equity is
a net number and a metric never contains an unrealised gain.

Shares are counted in raw terms, and a holding's value moves with the ANALYSIS series, so a split or
bonus changes the share count and not the value. Cash earns nothing (a conservative choice: a
liquid-fund yield would add to every arm alike). A position is never leveraged.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from math import floor

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.swing.costs import SwingCostModel
from emporos.research.swing.data import AsOfView, SwingDataset
from emporos.research.swing.rules import (
    AllMembers,
    DecisionContext,
    Holding,
    Intent,
    Membership,
    SwingStrategy,
)

__all__ = ["FillPrice", "SwingConfig", "SwingRun", "SwingSimulator", "Trade"]


class FillPrice(StrEnum):
    """Which price of the fill session the orders trade at. A stock cell decides at a close and
    trades the next OPEN (the default). A core ETF cell trades the next CLOSE (PROFIT_PLAN §10:
    the early ETF opening prints are noisy); costs are the same either way."""

    OPEN = "open"
    CLOSE = "close"


@dataclass(frozen=True)
class SwingConfig:
    capital: Decimal
    max_positions: int
    max_size_multiple: Decimal = Decimal(1)
    cash_yield: Decimal = Decimal(0)  # annual, accrued per session on cash held overnight
    start_day: date | None = None  # first session simulated; earlier bars are history a signal sees
    fill: FillPrice = FillPrice.OPEN

    @property
    def cash_growth_per_session(self) -> Decimal:
        """`(1 + cash_yield) ** (1 / 252)`: cash held into a session is multiplied by this."""
        return (1 + self.cash_yield) ** (Decimal(1) / 252)

    def __post_init__(self) -> None:
        if self.cash_yield < 0:
            raise ValueError("a cash yield is not negative")
        if self.capital <= 0:
            raise ValueError("capital is positive")
        if self.max_positions < 1:
            raise ValueError("a book holds at least one position")
        if self.max_size_multiple < 1:
            raise ValueError("the ceiling on a size multiple is at least the base, 1")


@dataclass(frozen=True)
class Trade:
    """One round trip. `net_pnl` is after both sides' fees and slippage."""

    instrument_id: str
    entry_day: date
    exit_day: date
    entry_price: Decimal  # raw fill, slippage included
    exit_price: Decimal
    quantity: int  # shares bought
    fees: Decimal  # both sides
    net_pnl: Decimal


@dataclass(frozen=True)
class SwingRun:
    """What a simulation produced. `equity[i]` is the close of `days[i]`, after that session's
    fills; the last value is after the final liquidation."""

    days: tuple[date, ...]
    equity: tuple[Decimal, ...]
    invested_days: int  # sessions that closed with at least one holding
    trades: tuple[Trade, ...]
    capital: Decimal
    fees: Decimal
    skipped_entries: int  # buys that did not fit, or whose name had no bar
    invested_flags: tuple[bool, ...] = ()  # per session: closed with at least one holding

    def slice_from(self, first: date) -> SwingRun:
        """The run from `first` on, as if it had started then with what the book held the session
        before: for reporting a sub-period. Trades are those bought on or after `first`."""
        start = next((i for i, d in enumerate(self.days) if d >= first), None)
        if start is None:
            raise ValueError(f"the run has no session on or after {first}")
        if start == 0:
            return self
        flags = self.invested_flags[start:] if self.invested_flags else ()
        trades = tuple(t for t in self.trades if t.entry_day >= first)
        return SwingRun(
            self.days[start:], self.equity[start:], sum(flags), trades, self.equity[start - 1],
            sum((t.fees for t in trades), Decimal(0)), 0, flags,
        )  # fmt: skip

    def slice_until(self, last: date) -> SwingRun:
        """The run up to and including `last`, marked to market there (nothing is liquidated): for
        reporting the first half of a window. Trades are those closed on or before `last`."""
        end = sum(1 for d in self.days if d <= last)
        if end == 0:
            raise ValueError(f"the run has no session on or before {last}")
        if end == len(self.days):
            return self
        flags = self.invested_flags[:end] if self.invested_flags else ()
        trades = tuple(t for t in self.trades if t.exit_day <= last)
        return SwingRun(
            self.days[:end], self.equity[:end], sum(flags), trades, self.capital,
            sum((t.fees for t in trades), Decimal(0)), 0, flags,
        )  # fmt: skip

    @property
    def days_in_cash(self) -> int:
        return len(self.days) - self.invested_days

    @property
    def daily_pnl(self) -> tuple[Decimal, ...]:
        """Rupee P&L per session, from the initial capital on the first."""
        previous = (self.capital, *self.equity[:-1])
        return tuple(now - before for now, before in zip(self.equity, previous, strict=True))

    @property
    def pnl_by_instrument(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for trade in self.trades:
            totals[trade.instrument_id] += trade.net_pnl
        return dict(totals)


@dataclass
class _Position:
    instrument_id: str
    quantity: int
    entry_index: int
    entry_day: date
    entry_price: Decimal
    entry_multiplier: Decimal  # analysis / raw on the entry session
    entry_equity: Decimal  # the book's equity when it was bought
    entry_fees: Decimal
    cost: Decimal  # cash spent, fees included


class SwingSimulator:
    def __init__(
        self,
        dataset: SwingDataset,
        strategy: SwingStrategy,
        config: SwingConfig,
        costs: SwingCostModel,
        membership: Membership | None = None,
    ) -> None:
        self._data = dataset
        self._strategy = strategy
        self._config = config
        self._costs = costs
        self._membership = membership or AllMembers()

    def run(self) -> SwingRun:
        calendar = self._data.calendar
        if not calendar:
            raise ValueError("there are no sessions to simulate")
        first = self._first_index(calendar)
        state = _State(self._config.capital)
        equity: list[Decimal] = []
        invested = 0
        flags: list[bool] = []
        pending_exits: set[str] = set()
        pending_entries: list[Intent] = []
        last = len(calendar) - 1
        growth = self._config.cash_growth_per_session
        for i in range(first, len(calendar)):
            day = calendar[i]
            if i > first:
                state.cash *= growth
            self._fill_exits(state, i, day, pending_exits)
            self._fill_entries(state, i, day, pending_entries)
            pending_entries = []
            if i == last:
                self._liquidate(state, i, day)
            value = state.cash + sum(self._value(p, day) for p in state.positions.values())
            equity.append(value)
            invested += bool(state.positions)
            flags.append(bool(state.positions))
            if i < last:
                pending_exits, pending_entries = self._decide(state, i, day, value)
        return SwingRun(
            calendar[first:], tuple(equity), invested, tuple(state.trades), self._config.capital,
            state.fees, state.skipped, tuple(flags),
        )  # fmt: skip

    def _first_index(self, calendar: tuple[date, ...]) -> int:
        start = self._config.start_day
        if start is None:
            return 0
        index = next((i for i, d in enumerate(calendar) if d >= start), None)
        if index is None:
            raise ValueError(f"the data has no session on or after {start}")
        return index

    # -- fills --------------------------------------------------------------------------------

    def _fill_exits(self, state: _State, i: int, day: date, exits: set[str]) -> None:
        for instrument_id in sorted(exits):
            position = state.positions.get(instrument_id)
            if position is None:
                exits.discard(instrument_id)
                continue
            bar = self._data.bar_on(instrument_id, day)
            if bar is None:
                continue  # no session for the name today: the order waits
            series = self._data.series(instrument_id)
            self._close(
                state, position, day, self._fill_price(series.raw[bar]), series.multipliers[bar]
            )
            exits.discard(instrument_id)

    def _fill_entries(self, state: _State, i: int, day: date, intents: list[Intent]) -> None:
        for intent in intents[: self._config.max_positions]:
            if intent.instrument_id in state.positions:
                continue
            if len(state.positions) >= self._config.max_positions:
                return
            bar = self._data.bar_on(intent.instrument_id, day)
            if bar is None:
                state.skipped += 1
                continue
            series = self._data.series(intent.instrument_id)
            self._buy(
                state, intent, i, day, self._fill_price(series.raw[bar]), series.multipliers[bar]
            )

    def _fill_price(self, raw: Candle) -> Decimal:
        return raw.open.amount if self._config.fill is FillPrice.OPEN else raw.close.amount

    def _buy(
        self,
        state: _State,
        intent: Intent,
        i: int,
        day: date,
        raw_fill: Decimal,
        multiplier: Decimal,
    ) -> None:
        multiple = min(intent.size_multiple, self._config.max_size_multiple)
        at_open = self._config.fill is FillPrice.OPEN
        equity_now = state.cash + sum(
            self._value(p, day, at_open=at_open) for p in state.positions.values()
        )
        allocation = min(equity_now * multiple / self._config.max_positions, state.cash)
        price = self._costs.buy_price(raw_fill)
        quantity = floor(allocation / price) if price > 0 else 0
        fees = Decimal(0)
        while quantity > 0:
            fees = self._costs.fees(OrderSide.BUY, quantity, price)
            if quantity * price + fees <= allocation:
                break
            quantity -= 1
        if quantity < 1:
            state.skipped += 1
            return
        cost = quantity * price + fees
        state.cash -= cost
        state.fees += fees
        state.positions[intent.instrument_id] = _Position(
            intent.instrument_id, quantity, i, day, price, multiplier, equity_now, fees, cost
        )

    def _liquidate(self, state: _State, i: int, day: date) -> None:
        for instrument_id in sorted(state.positions):
            position = state.positions[instrument_id]
            bar = self._data.series(instrument_id).last_index_on_or_before(day)
            assert bar is not None  # it was bought on a session, so it has a bar
            series = self._data.series(instrument_id)
            self._close(state, position, day, series.raw[bar].close.amount, series.multipliers[bar])

    def _close(
        self, state: _State, position: _Position, day: date, raw_price: Decimal, multiplier: Decimal
    ) -> None:
        """Sell the whole position at `raw_price` (a raw price on the sale session's basis)."""
        shares = Decimal(position.quantity) * multiplier / position.entry_multiplier
        sell_price = self._costs.sell_price(raw_price)
        whole = max(1, int(shares.to_integral_value()))
        fees = self._costs.fees(OrderSide.SELL, whole, sell_price)
        proceeds = shares * sell_price - fees
        state.cash += proceeds
        state.fees += fees
        state.trades.append(
            Trade(
                position.instrument_id,
                position.entry_day,
                day,
                position.entry_price,
                sell_price,
                position.quantity,
                position.entry_fees + fees,
                proceeds - position.cost,
            )  # fmt: skip
        )
        del state.positions[position.instrument_id]

    # -- valuation and decisions --------------------------------------------------------------

    def _value(self, position: _Position, day: date, *, at_open: bool = False) -> Decimal:
        """The position's worth on `day` on the analysis basis, at the open or the close. A name
        with no bar on `day` is valued at its last close."""
        series = self._data.series(position.instrument_id)
        bar = series.last_index_on_or_before(day)
        assert bar is not None
        analysis = series.analysis[bar]
        price = (
            analysis.open.amount if at_open and series.days[bar] == day else analysis.close.amount
        )
        return Decimal(position.quantity) / position.entry_multiplier * price

    def _decide(
        self, state: _State, i: int, day: date, equity: Decimal
    ) -> tuple[set[str], list[Intent]]:
        tradable = frozenset(
            n for n in self._data.instrument_ids
            if self._data.bar_on(n, day) is not None and self._membership.is_member(n, day)
        )  # fmt: skip
        holdings = {
            n: Holding(
                n,
                p.entry_index,
                p.entry_day,
                i - p.entry_index,
                p.entry_price * p.entry_multiplier,
                p.quantity * p.entry_price,
                p.entry_equity,
            )
            for n, p in state.positions.items()
        }
        context = DecisionContext(day, i, AsOfView(self._data, day), holdings, equity, tradable)
        intents = list(self._strategy.desired(context))[: self._config.max_positions]
        for intent in intents:
            if intent.instrument_id not in self._data.instrument_ids:
                raise ValueError(
                    f"the strategy wants {intent.instrument_id}, which is not in the data"
                )
        wanted = {intent.instrument_id for intent in intents}
        exits = set(state.positions) - wanted
        entries = [
            intent for intent in intents
            if intent.instrument_id not in state.positions and intent.instrument_id in tradable
        ]  # fmt: skip
        return exits, entries


@dataclass
class _State:
    cash: Decimal
    positions: dict[str, _Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    fees: Decimal = Decimal(0)
    skipped: int = 0
