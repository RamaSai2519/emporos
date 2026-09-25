"""The program's own cost schedules behind the replay's `TradeCosts` (EM-240, PROFIT_PLAN §12.5).

One class per kind of trade, each pricing a round trip in rupees under the same scenarios the rest
of the program is judged by, and `RoutedCosts` picks the one a trade's instrument needs:

* intraday cash: `ScreenCostModel` with the benchmark file's scenarios (5 bps marketable-limit
  slippage per side already in the run, plus the scenario's extra, fees x1.5 for adverse);
* delivery (cash swing): `SwingCostModel` on the earliest delivery schedule (as every swing
  screen), BENCHMARK 10 bps a side, ADVERSE 25 bps and fees x1.5;
* a bought option: `FoCostModel` and the options slippage scenarios (one tick plus 0.5% of the
  premium a fill, adverse two ticks plus 1.5% and fees x1.5), one order per side.

Slippage is charged here, as a cost, never baked into a fill price (fills are reference prices)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from emporos.backtest.costs import ScheduleSource
from emporos.backtest.robustness.benchmark import BenchmarkConfig
from emporos.core.clock import IST
from emporos.domain.fees import FeeSchedule
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.eventtrader.replay.costs import TradeCosts
from emporos.eventtrader.replay.records import Scenario, TradeLeg
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.options.chain import OptionRight
from emporos.options.fo_costs import FoCostModel, FoFeeSchedule
from emporos.options.slippage import ADVERSE as OPTION_ADVERSE
from emporos.options.slippage import BENCHMARK as OPTION_BENCHMARK
from emporos.options.slippage import SlippageScenario
from emporos.options.spread import Leg, LegFill
from emporos.research.screen_costs import ScreenCostModel, ScreenCostScenario
from emporos.research.screen_trades import ScreenTrade
from emporos.research.swing.costs import ADVERSE as SWING_ADVERSE
from emporos.research.swing.costs import BENCHMARK as SWING_BENCHMARK
from emporos.research.swing.costs import CostScenario, SwingCostModel

__all__ = ["IntradayCosts", "OptionCosts", "RoutedCosts", "SwingCosts", "program_costs"]

OPTION_TICK = Decimal("0.05")
_SCENARIO_NAMES = {Scenario.BENCHMARK: "benchmark", Scenario.ADVERSE: "adverse"}


def _order_side(side: Side) -> OrderSide:
    return OrderSide.BUY if side is Side.LONG else OrderSide.SELL


class IntradayCosts:
    def __init__(self, model: ScreenCostModel, benchmark: BenchmarkConfig) -> None:
        self._model = model
        self._scenarios = {
            s: ScreenCostScenario.from_benchmark(benchmark, name)
            for s, name in _SCENARIO_NAMES.items()
        }

    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal:
        trade = ScreenTrade(
            leg.name, leg.entry_ts.astimezone(IST).date(), _order_side(leg.side),
            Money(leg.entry_price), Money(leg.exit_price),
        )  # fmt: skip
        fraction = self._model.round_trip_fraction(trade, leg.quantity, self._scenarios[scenario])
        return fraction * leg.entry_price * leg.quantity


class SwingCosts:
    """Delivery. Only a long is priced: a cash swing short is refused by the risk engine (it is a
    put), so a short here is a bug and raises."""

    def __init__(
        self, schedule: FeeSchedule, scenarios: Mapping[Scenario, CostScenario] | None = None
    ) -> None:
        self._schedule = schedule
        self._scenarios = dict(
            scenarios or {Scenario.BENCHMARK: SWING_BENCHMARK, Scenario.ADVERSE: SWING_ADVERSE}
        )

    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal:
        if leg.side is not Side.LONG:
            raise ValueError("delivery costs are defined for a long only")
        model = SwingCostModel(self._schedule, self._scenarios[scenario])
        buy, sell = model.buy_price(leg.entry_price), model.sell_price(leg.exit_price)
        slippage = (buy - leg.entry_price + leg.exit_price - sell) * leg.quantity
        fees = model.fees(OrderSide.BUY, leg.quantity, buy) + model.fees(
            OrderSide.SELL, leg.quantity, sell
        )
        return slippage + fees


class OptionCosts:
    """A bought call or put: buy at entry, sell at exit, each its own order. `quantity` is units
    (lots x lot size); the entry and exit prices are the marks, before slippage."""

    def __init__(
        self,
        schedule: FoFeeSchedule,
        scenarios: Mapping[Scenario, SlippageScenario] | None = None,
    ) -> None:
        self._schedule = schedule
        self._scenarios = dict(
            scenarios or {Scenario.BENCHMARK: OPTION_BENCHMARK, Scenario.ADVERSE: OPTION_ADVERSE}
        )

    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal:
        slip = self._scenarios[scenario]
        model = FoCostModel(self._schedule, slip.fee_multiplier)
        right = OptionRight.CALL if leg.instrument is Instrument.CALL else OptionRight.PUT
        # Charges depend on the side and the turnover only, so the strike is not needed here.
        bought = slip.fill_price(OrderSide.BUY, leg.entry_price, OPTION_TICK)
        sold = slip.fill_price(OrderSide.SELL, leg.exit_price, OPTION_TICK)
        charges = model.orders(
            [
                LegFill(Leg(Decimal(0), right, OrderSide.BUY), bought, leg.quantity),
                LegFill(Leg(Decimal(0), right, OrderSide.SELL), sold, leg.quantity),
            ]
        )
        return (bought - leg.entry_price + leg.exit_price - sold) * leg.quantity + charges.total


class RoutedCosts:
    """Picks the cost model by the trade's instrument; an unpriced instrument is an error, never a
    free trade."""

    def __init__(self, by_instrument: Mapping[Instrument, TradeCosts]) -> None:
        self._by_instrument = dict(by_instrument)

    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal:
        try:
            model = self._by_instrument[leg.instrument]
        except KeyError:
            raise ValueError(f"no cost model for {leg.instrument.value}") from None
        return model.cost(leg, scenario)


def program_costs(
    schedules: ScheduleSource,
    benchmark: BenchmarkConfig,
    delivery: FeeSchedule,
    fo_schedule: FoFeeSchedule,
) -> RoutedCosts:
    intraday = IntradayCosts(ScreenCostModel(schedules), benchmark)
    swing, options = SwingCosts(delivery), OptionCosts(fo_schedule)
    return RoutedCosts(
        {
            Instrument.CASH_INTRADAY: intraday,
            Instrument.CASH_SWING: swing,
            Instrument.CALL: options,
            Instrument.PUT: options,
        }
    )
