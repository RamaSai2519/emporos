"""`MetricsCalculator` — assembles the full metrics report from what a run produced.

    starting cash, equity curve, closed trades, traded notional
        ──▶ daily series ──▶ ratios, monthly table, turnover
        ──▶ drawdowns, exposure, trade statistics            ──▶ MetricsReport

Each part is its own small analyzer, injected, so a new metric is a new analyzer, not an edit to
the others. Nothing here rounds: rendering to a fixed number of places is `MetricsDocument`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from emporos.backtest.metrics.breakdown import (
    RegimeTimeline,
    TradeGrouper,
    direction_key,
    instrument_key,
    regime_key_factory,
    time_of_day_key,
)
from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.metrics.drawdown import DrawdownAnalyzer, DrawdownReport
from emporos.backtest.metrics.equity import DailySeries
from emporos.backtest.metrics.exposure import (
    ExposureAnalyzer,
    ExposureStatistics,
    TurnoverAnalyzer,
    TurnoverStatistics,
)
from emporos.backtest.metrics.monthly import MonthlyReturn, MonthlyTable
from emporos.backtest.metrics.ratios import RatioCalculator, ReturnRatios
from emporos.backtest.metrics.trades import TradeAnalyzer, TradeStatistics
from emporos.backtest.portfolio import ClosedTrade, EquityPoint
from emporos.domain.money import Money


@dataclass(frozen=True)
class MetricsSettings:
    annualisation_days: int = 252
    risk_free_annual: Decimal = field(default=ZERO)


@dataclass(frozen=True)
class MetricsReport:
    settings: MetricsSettings
    starting_cash: Money
    ending_equity: Money
    trading_days: int
    returns: ReturnRatios
    calmar: Decimal | None
    drawdown: DrawdownReport
    trades: TradeStatistics
    exposure: ExposureStatistics
    turnover: TurnoverStatistics
    monthly: tuple[MonthlyReturn, ...]
    by_direction: dict[str, TradeStatistics]
    by_instrument: dict[str, TradeStatistics]
    by_time_of_day: dict[str, TradeStatistics]
    by_regime: dict[str, TradeStatistics]
    by_direction_instrument: dict[str, dict[str, TradeStatistics]]
    by_direction_time_of_day: dict[str, dict[str, TradeStatistics]]
    by_direction_regime: dict[str, dict[str, TradeStatistics]]
    daily_returns: tuple[Decimal, ...] = ()  # one per trading day, in order; not in the document


class MetricsCalculator:
    def __init__(
        self,
        settings: MetricsSettings | None = None,
        daily: DailySeries | None = None,
        ratios: RatioCalculator | None = None,
        drawdowns: DrawdownAnalyzer | None = None,
        trades: TradeAnalyzer | None = None,
        exposure: ExposureAnalyzer | None = None,
        turnover: TurnoverAnalyzer | None = None,
        monthly: MonthlyTable | None = None,
        grouper: TradeGrouper | None = None,
    ) -> None:
        self._settings = settings or MetricsSettings()
        self._daily = daily or DailySeries()
        self._ratios = ratios or RatioCalculator(
            self._settings.annualisation_days, self._settings.risk_free_annual
        )
        self._drawdowns = drawdowns or DrawdownAnalyzer()
        self._trades = trades or TradeAnalyzer()
        self._exposure = exposure or ExposureAnalyzer()
        self._turnover = turnover or TurnoverAnalyzer()
        self._monthly = monthly or MonthlyTable()
        self._grouper = grouper or TradeGrouper(self._trades)

    def calculate(
        self,
        starting_cash: Money,
        curve: Sequence[EquityPoint],
        closed_trades: Sequence[ClosedTrade],
        traded_notional: Money,
        regime_timelines: Mapping[str, RegimeTimeline] | None = None,
    ) -> MetricsReport:
        start = starting_cash.amount
        days = self._daily.build(start, curve)
        returns = self._ratios.calculate(start, days)
        drawdown = self._drawdowns.analyze(start, curve)
        by_regime = (
            self._grouper.group(closed_trades, regime_key_factory(regime_timelines))
            if regime_timelines
            else {}
        )
        by_direction_regime = (
            self._grouper.group_cross(
                closed_trades, direction_key, regime_key_factory(regime_timelines)
            )
            if regime_timelines
            else {}
        )
        return MetricsReport(
            settings=self._settings,
            starting_cash=starting_cash,
            ending_equity=curve[-1].equity if curve else starting_cash,
            trading_days=len(days),
            returns=returns,
            calmar=self._calmar(returns, drawdown),
            drawdown=drawdown,
            trades=self._trades.analyze(closed_trades),
            exposure=self._exposure.analyze(curve),
            turnover=self._turnover.analyze(traded_notional, days, returns.years),
            monthly=self._monthly.build(start, days),
            by_direction=self._grouper.group(closed_trades, direction_key),
            by_instrument=self._grouper.group(closed_trades, instrument_key),
            by_time_of_day=self._grouper.group(closed_trades, time_of_day_key),
            by_regime=by_regime,
            by_direction_instrument=self._grouper.group_cross(
                closed_trades, direction_key, instrument_key
            ),
            by_direction_time_of_day=self._grouper.group_cross(
                closed_trades, direction_key, time_of_day_key
            ),
            by_direction_regime=by_direction_regime,
            daily_returns=tuple(day.ret for day in days),
        )

    @staticmethod
    def _calmar(returns: ReturnRatios, drawdown: DrawdownReport) -> Decimal | None:
        if returns.cagr is None or drawdown.max_drawdown == ZERO:
            return None
        return DecimalMath.divide(returns.cagr, drawdown.max_drawdown)
