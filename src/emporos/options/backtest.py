"""The end-of-day spread backtester (EM-226, PROFIT_PLAN.md §5 B-F2).

Once a day, at the close: manage every open spread (settle it on its expiry day, otherwise let the
exit policy decide), then, if the depth policy allows another, plan at most one entry. The margin
of every open spread counts against the capital. Fills are at the day's close plus the slippage
scenario's assumption, never at a price the contract did not trade at: a leg that did not trade
that day blocks an entry and defers an exit (on its expiry day a spread settles at intrinsic value
regardless). Every trade and every day is returned, so a caller can draw the daily P&L and the
equity curve of each arm.

What this cannot know, and does not pretend to: intraday prices, the true bid-ask, implied
volatility, the broker's real margin, or a fill's queue position. Slippage, margin and
STT-on-exercise are ASSUMPTIONS stated in `slippage.py`, `margin.py` and the fee file
(`verified: false`)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.domain.orders import OrderSide
from emporos.options.chain import ChainSnapshot, ExpiryChain, OptionQuote, OptionRight
from emporos.options.chain_source import ChainSource
from emporos.options.depth import DepthPolicy, FixedDepth
from emporos.options.entry import EntryPlanner
from emporos.options.exits import ExitPolicy, ExitReason, OpenView
from emporos.options.fo_costs import FoCharges, FoCostModel, FoFeeSchedule
from emporos.options.margin import MarginEstimator, max_loss
from emporos.options.slippage import SlippageScenario
from emporos.options.spread import Leg, LegFill, SpreadPlan

__all__ = [
    "BacktestResult",
    "BacktestSettings",
    "DayPnl",
    "MissingExpirySnapshot",
    "MissingSettlement",
    "SpreadBacktester",
    "SpreadTrade",
]

_OPPOSITE = {OrderSide.BUY: OrderSide.SELL, OrderSide.SELL: OrderSide.BUY}


class MissingSettlement(RuntimeError):
    """A spread reached its expiry day and the chain carries no final settlement level for it. A
    spread must settle on the exchange's own number, never on an approximation, so the run stops."""


class MissingExpirySnapshot(RuntimeError):
    """A spread was open past its expiry with no chain on the expiry day: the data has a hole
    exactly where the result is decided, so the run stops instead of guessing a settlement."""


@dataclass(frozen=True)
class BacktestSettings:
    capital: Decimal
    lots: int
    slippage: SlippageScenario
    min_contracts: int = 1  # lots that must have traded on each leg's contract to open a spread

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.lots < 1 or self.min_contracts < 1:
            raise ValueError("lots and the liquidity floor must be at least 1")


@dataclass(frozen=True)
class SpreadTrade:
    entry_day: date
    exit_day: date
    plan: SpreadPlan
    lots: int
    units: int
    credit_per_unit: Decimal
    close_debit_per_unit: Decimal
    reason: ExitReason
    gross_pnl: Decimal
    charges: Decimal  # entry and exit together
    margin: Decimal
    max_loss: Decimal

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.charges

    @property
    def days_held(self) -> int:
        return (self.exit_day - self.entry_day).days


@dataclass(frozen=True)
class DayPnl:
    day: date
    equity: Decimal
    pnl: Decimal  # the day's change in equity, marked to the close
    open_positions: int = 0  # spreads still open after the day's trading


@dataclass(frozen=True)
class BacktestResult:
    trades: tuple[SpreadTrade, ...]
    days: tuple[DayPnl, ...]
    skipped: Mapping[str, int]  # why entries and exits did not happen, by reason

    @property
    def net_pnl(self) -> Decimal:
        return sum((t.net_pnl for t in self.trades), Decimal(0))

    def daily_pnl(self) -> list[Decimal]:
        return [d.pnl for d in self.days]

    def monthly_pnl(self) -> dict[tuple[int, int], Decimal]:
        months: dict[tuple[int, int], Decimal] = {}
        for d in self.days:
            key = (d.day.year, d.day.month)
            months[key] = months.get(key, Decimal(0)) + d.pnl
        return months

    def max_drawdown(self) -> Decimal:
        """The largest peak-to-trough fall of the equity curve, as a fraction of the peak."""
        peak, worst = Decimal(0), Decimal(0)
        for d in self.days:
            peak = max(peak, d.equity)
            if peak > 0:
                worst = max(worst, (peak - d.equity) / peak)
        return worst


@dataclass
class _Open:
    plan: SpreadPlan
    lots: int
    units: int
    entry_day: date
    credit_per_unit: Decimal
    entry_charges: FoCharges
    margin: Decimal
    worst_case: Decimal
    last_mark_debit: Decimal


class SpreadBacktester:
    def __init__(
        self,
        chains: ChainSource,
        entry: EntryPlanner,
        exits: ExitPolicy,
        margin: MarginEstimator,
        fees: FoFeeSchedule,
        settings: BacktestSettings,
        depth: DepthPolicy | None = None,
    ) -> None:
        self._depth = depth or FixedDepth(1)
        self._chains = chains
        self._entry = entry
        self._exits = exits
        self._margin = margin
        self._settings = settings
        self._costs = FoCostModel(fees, settings.slippage.fee_multiplier)

    def run(self, first: date, last: date) -> BacktestResult:
        trades: list[SpreadTrade] = []
        days: list[DayPnl] = []
        skipped: Counter[str] = Counter()
        cash = Decimal(0)  # realised P&L after costs, plus the entry costs of what is open
        equity = self._settings.capital
        held: list[_Open] = []
        for day in self._chains.days():
            if not first <= day <= last:
                continue
            snapshot = self._chains.snapshot(day)
            if snapshot is None:
                skipped["no_chain"] += 1
                continue
            for position in held:
                if day > position.plan.expiry:
                    raise MissingExpirySnapshot(
                        f"open past {position.plan.expiry}, first chain {day}"
                    )
            still_open: list[_Open] = []
            for position in held:
                trade, cash = self._manage(position, snapshot, cash, skipped)
                if trade is None:
                    still_open.append(position)
                else:
                    trades.append(trade)
            held = still_open
            if len(held) < self._depth.limit(snapshot):
                opened, cash = self._enter(snapshot, cash, held, skipped)
                if opened is not None:
                    held.append(opened)
            elif self._entry.plan(snapshot) is not None:
                skipped["ladder_full"] += 1
            marked = self._capital_now(held, snapshot, cash)
            days.append(DayPnl(day, marked, marked - equity, len(held)))
            equity = marked
        return BacktestResult(tuple(trades), tuple(days), dict(skipped))

    # --- the open position ----------------------------------------------------------------------
    def _manage(
        self, held: _Open, snapshot: ChainSnapshot, cash: Decimal, skipped: Counter[str]
    ) -> tuple[SpreadTrade | None, Decimal]:
        if snapshot.day == held.plan.expiry:
            return self._settle(held, snapshot, cash)
        chain = snapshot.expiries.get(held.plan.expiry)
        quotes = self._quotes(held.plan, chain)
        if quotes is None:
            skipped["unmarked"] += 1
            return None, cash
        debit = self._close_debit(held.plan, quotes)
        held.last_mark_debit = debit
        view = OpenView(held.credit_per_unit, debit, snapshot.days_to(held.plan.expiry))
        reason = self._exits.decide(view)
        if reason is None:
            return None, cash
        if not all(q.tradable for q in quotes.values()):
            skipped["exit_deferred_untraded_leg"] += 1
            return None, cash
        fills = [
            self._fill(leg, _OPPOSITE[leg.side], quotes[leg], snapshot.tick_size, held.units)
            for leg in held.plan.legs
        ]
        exit_debit = sum(
            (f.price if f.leg.side is OrderSide.BUY else -f.price for f in fills), Decimal(0)
        )
        charges = self._costs.orders(fills)
        return self._close(held, snapshot.day, exit_debit, charges, reason, cash)

    def _settle(
        self, held: _Open, snapshot: ChainSnapshot, cash: Decimal
    ) -> tuple[SpreadTrade | None, Decimal]:
        spot = snapshot.settlements.get(held.plan.expiry)
        if spot is None:
            raise MissingSettlement(f"no settlement level for {held.plan.expiry} in {snapshot.day}")
        debit = held.plan.settlement_debit(spot)
        intrinsic = Decimal(0)
        for vertical in held.plan.verticals:
            long_in_money = (
                vertical.long_strike - spot
                if vertical.right is OptionRight.PUT
                else spot - vertical.long_strike
            )
            intrinsic += max(Decimal(0), long_in_money) * held.units
        charges = self._costs.expiry_stt(intrinsic)
        return self._close(held, snapshot.day, debit, charges, ExitReason.EXPIRY, cash)

    def _close(
        self,
        held: _Open,
        day: date,
        exit_debit: Decimal,
        exit_charges: FoCharges,
        reason: ExitReason,
        cash: Decimal,
    ) -> tuple[SpreadTrade, Decimal]:
        gross = (held.credit_per_unit - exit_debit) * held.units
        trade = SpreadTrade(
            held.entry_day, day, held.plan, held.lots, held.units, held.credit_per_unit,
            exit_debit, reason, gross, held.entry_charges.total + exit_charges.total,
            held.margin, held.worst_case,
        )  # fmt: skip
        return trade, cash + gross - exit_charges.total

    # --- a new position -------------------------------------------------------------------------
    def _enter(
        self, snapshot: ChainSnapshot, cash: Decimal, held: list[_Open], skipped: Counter[str]
    ) -> tuple[_Open | None, Decimal]:
        plan = self._entry.plan(snapshot)
        if plan is None:
            skipped["no_plan"] += 1
            return None, cash
        quotes = self._quotes(plan, snapshot.expiries.get(plan.expiry))
        floor = self._settings.min_contracts
        if quotes is None or any(
            q.contracts_traded < floor or not q.tradable for q in quotes.values()
        ):
            skipped["not_tradable"] += 1
            return None, cash
        units = self._settings.lots * snapshot.lot_size
        fills = [
            self._fill(leg, leg.side, quotes[leg], snapshot.tick_size, units) for leg in plan.legs
        ]
        credit = sum(
            (f.price if f.leg.side is OrderSide.SELL else -f.price for f in fills), Decimal(0)
        )
        if credit <= 0 or credit >= plan.max_width:
            skipped["no_credit" if credit <= 0 else "credit_beyond_width"] += 1
            return None, cash
        margin = self._margin.required(plan, credit, units)
        if margin + sum((p.margin for p in held), Decimal(0)) > self._settings.capital + cash:
            skipped["margin"] += 1
            return None, cash
        charges = self._costs.orders(fills)
        opened = _Open(
            plan, self._settings.lots, units, snapshot.day, credit, charges, margin,
            max_loss(plan, credit, units), credit,
        )  # fmt: skip
        return opened, cash - charges.total

    # --- shared ---------------------------------------------------------------------------------
    def _quotes(self, plan: SpreadPlan, chain: ExpiryChain | None) -> dict[Leg, OptionQuote] | None:
        if chain is None:
            return None
        found: dict[Leg, OptionQuote] = {}
        for leg in plan.legs:
            quote = chain.quote(leg.strike, leg.right)
            if quote is None:
                return None
            found[leg] = quote
        return found

    def _fill(
        self, leg: Leg, side: OrderSide, quote: OptionQuote, tick: Decimal, units: int
    ) -> LegFill:
        price = self._settings.slippage.fill_price(side, quote.close, tick)
        return LegFill(Leg(leg.strike, leg.right, side), price, units)

    @staticmethod
    def _close_debit(plan: SpreadPlan, quotes: Mapping[Leg, OptionQuote]) -> Decimal:
        """Per unit, what closing costs: buy back the shorts, sell the longs, at the marks."""
        total = Decimal(0)
        for leg in plan.legs:
            mark = quotes[leg].mark
            total += mark if leg.side is OrderSide.SELL else -mark
        return total

    def _capital_now(self, held: list[_Open], snapshot: ChainSnapshot, cash: Decimal) -> Decimal:
        equity = self._settings.capital + cash
        for position in held:
            quotes = self._quotes(position.plan, snapshot.expiries.get(position.plan.expiry))
            if quotes is not None:
                position.last_mark_debit = self._close_debit(position.plan, quotes)
            equity += (position.credit_per_unit - position.last_mark_debit) * position.units
        return equity
