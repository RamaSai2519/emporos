"""Portfolio economics at the canonical ₹50,000 production capital (EM-183): what fragmenting
that capital across several CONCURRENT positions does to the cost a trade must clear before it is
worth taking, and whether a strategy's observed edge is large enough to survive plausible error in
the cost model itself.

Deliberately separate from `emporos.research.costs.TransactionCostModel` (EM-178, unchanged,
still what every feature/cross-sectional/lead-lag study uses to cost-adjust ONE trade at a given
quantity): that model answers "what does this one trade cost"; this module answers the PORTFOLIO
question "what does splitting capital across N concurrent positions do to that cost, and is the
edge this strategy showed economically real once that fragmentation and a safety margin for cost
error are both accounted for". `PortfolioCostModel` composes `IntradayCharges` — the same
statutory-charges source EM-178 uses — directly; it never invents a new fee schedule, and it never
imports a strategy's own parameters (`emporos.strategies.config`) or any alpha-signal type: capital
and risk assumptions are a fact about the account, not about which strategy is trading it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.portfolio import ClosedTrade
from emporos.domain.fees import FeeSchedule, IntradayCharges
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

_ZERO = Decimal(0)
_BASIS_POINTS = Decimal(10_000)

# The one figure every production-capital backtest, walk-forward window and paper/live
# conservative-allocation run starts from (mirrors `emporos.opportunity.capital.PRODUCTION_CAPITAL`
# — duplicated, not imported, so this offline-analysis module never depends on the orchestration
# layer; both are pinned to the same ₹50,000 by the same discipline: a deliberate, reviewed change,
# never a config default that could drift).
PRODUCTION_CAPITAL = Money(Decimal(50_000))


@dataclass(frozen=True)
class CostComponents:
    """One round trip's cost, broken into what the acceptance criteria ask to see reported
    separately: brokerage, the OTHER statutory charges (STT, exchange transaction, SEBI, stamp
    duty, GST — everything `ChargeBreakdown.total` has besides brokerage), spread and slippage."""

    notional: Money
    brokerage: Money
    statutory: Money
    spread: Money
    slippage: Money

    def __post_init__(self) -> None:
        if self.notional <= Money.zero():
            raise ValueError("notional must be positive")

    @property
    def total(self) -> Money:
        return self.brokerage + self.statutory + self.spread + self.slippage

    def bps(self, amount: Money) -> Decimal:
        return DecimalMath.divide(amount.amount * _BASIS_POINTS, self.notional.amount)

    @property
    def total_bps(self) -> Decimal:
        return self.bps(self.total)


class PortfolioCostModel:
    """Spread and slippage are configured separately (both in basis points, applied on both legs
    of the round trip, same mechanism as `TransactionCostModel.slippage_bps`) so a report can show
    which of the four cost components is doing the damage, not just their sum."""

    def __init__(self, schedule: FeeSchedule, spread_bps: Decimal, slippage_bps: Decimal) -> None:
        if spread_bps < _ZERO or slippage_bps < _ZERO:
            raise ValueError("spread and slippage cannot be negative")
        self._charges = IntradayCharges(schedule)
        self._spread_bps = spread_bps
        self._slippage_bps = slippage_bps

    def components_for(self, exchange: Exchange, quantity: int, price: Money) -> CostComponents:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        buy = self._charges.for_trade(exchange, OrderSide.BUY, quantity, price)
        sell = self._charges.for_trade(exchange, OrderSide.SELL, quantity, price)
        notional = price.times(quantity)
        brokerage = buy.brokerage + sell.brokerage
        statutory = (buy.total - buy.brokerage) + (sell.total - sell.brokerage)
        return CostComponents(
            notional=notional,
            brokerage=brokerage,
            statutory=statutory,
            spread=notional.times(self._round_trip_fraction(self._spread_bps)),
            slippage=notional.times(self._round_trip_fraction(self._slippage_bps)),
        )

    @staticmethod
    def _round_trip_fraction(bps: Decimal) -> Decimal:
        return DecimalMath.divide(bps * 2, _BASIS_POINTS)


@dataclass(frozen=True)
class FragmentationScenario:
    """One "what if this strategy ran alongside N-1 others" scenario: the SAME production capital
    split N ways, at one representative price. `costs` and `minimum_gross_edge_bps` are `None`
    when the fragment is too small to buy even one share — a real, reportable outcome for a
    high-priced instrument at high fragmentation, not an error."""

    concurrent_positions: int
    capital_per_position: Money
    quantity: int
    costs: CostComponents | None
    minimum_gross_edge_bps: Decimal | None


class PortfolioEconomics:
    def __init__(self, cost_model: PortfolioCostModel, capital: Money = PRODUCTION_CAPITAL) -> None:
        if capital <= Money.zero():
            raise ValueError("capital must be positive")
        self._cost_model = cost_model
        self._capital = capital

    def fragmentation_scenario(
        self, concurrent_positions: int, exchange: Exchange, price: Money
    ) -> FragmentationScenario:
        if concurrent_positions < 1:
            raise ValueError("need at least one concurrent position")
        capital_per_position = Money(
            DecimalMath.divide(self._capital.amount, Decimal(concurrent_positions))
        )
        quantity = int(capital_per_position.amount // price.amount)
        if quantity < 1:
            return FragmentationScenario(concurrent_positions, capital_per_position, 0, None, None)
        costs = self._cost_model.components_for(exchange, quantity, price)
        return FragmentationScenario(
            concurrent_positions, capital_per_position, quantity, costs, costs.total_bps
        )


@dataclass(frozen=True)
class TurnoverReport:
    trade_count: int
    total_turnover: Money  # entry + exit notional, every trade
    average_daily_turnover: Money
    capital_utilization: Decimal  # time-weighted deployed capital / (capital * trading days)


class TurnoverCalculator:
    """Capital utilization is time-weighted: a ₹10,000 position held for six hours counts for more
    than the same size held for six minutes, so a strategy that churns small size all day is not
    mistaken for one that commits real capital."""

    def evaluate(
        self, trades: Sequence[ClosedTrade], capital: Money, trading_days: int
    ) -> TurnoverReport:
        if trading_days < 1:
            raise ValueError("need at least one trading day")
        if capital <= Money.zero():
            raise ValueError("capital must be positive")
        if not trades:
            return TurnoverReport(0, Money.zero(), Money.zero(), _ZERO)
        total_turnover = Money.zero()
        deployed_capital_days = _ZERO
        for trade in trades:
            exit_notional = trade.exit_price.times(trade.quantity)
            total_turnover = total_turnover + trade.entry_notional + exit_notional
            duration_days = DecimalMath.divide(
                Decimal((trade.closed_at - trade.opened_at).total_seconds()), Decimal(86_400)
            )
            deployed_capital_days += trade.entry_notional.amount * duration_days
        average_daily_turnover = Money(
            DecimalMath.divide(total_turnover.amount, Decimal(trading_days))
        )
        available_capital_days = capital.amount * Decimal(trading_days)
        utilization = DecimalMath.divide(deployed_capital_days, available_capital_days)
        return TurnoverReport(len(trades), total_turnover, average_daily_turnover, utilization)
