"""The S2 screen (EM-191 F3, EDGE_SEARCH_PLAN.md §6): does a scan's trades clear the fixed bar?

`ScreenBar` holds the plan's S2 numbers. They are the plan's, not the agent's: a test pins them,
and a change is a commit by the operator that says why (§3.3). A near miss is a reject.

The screener is triage. A screen that has not been proven against `backtest curate` (F3b's parity
test) is `advisory`: it may rank ideas but its result is not a cell verdict.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.research.screen_costs import ScreenCostModel, ScreenCostScenario
from emporos.research.screen_trades import PositionSizer, ScreenTrade

__all__ = ["ScreenBar", "ScreenCheck", "ScreenEvaluator", "ScreenResult"]

_ZERO = Decimal(0)


@dataclass(frozen=True)
class ScreenBar:
    """Plan §6 stage S2. All conditions must hold."""

    min_net_t: Decimal = Decimal(3)  # Harvey-Liu-Zhu hurdle for a new factor
    min_trades: int = 300
    min_positive_year_share: Decimal = Decimal("0.6")
    max_top_instrument_share: Decimal = Decimal("0.5")  # of net profit


@dataclass(frozen=True)
class ScreenCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ScreenResult:
    trades: int
    skipped_unaffordable: int
    gross_mean: Decimal | None  # per trade, fraction of position value
    net_mean: Decimal | None  # under the benchmark scenario
    net_t: Decimal | None
    adverse_break_even: Decimal | None  # mean adverse-scenario cost per trade
    positive_year_share: Decimal | None
    top_instrument_share: Decimal | None
    checks: tuple[ScreenCheck, ...]
    advisory: bool

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if not c.passed)


class ScreenEvaluator:
    def __init__(
        self,
        costs: ScreenCostModel,
        benchmark: ScreenCostScenario,
        adverse: ScreenCostScenario,
        bar: ScreenBar | None = None,
    ) -> None:
        self._costs = costs
        self._benchmark = benchmark
        self._adverse = adverse
        self._bar = bar or ScreenBar()

    def evaluate(
        self, trades: Sequence[ScreenTrade], sizer: PositionSizer, *, advisory: bool
    ) -> ScreenResult:
        sized = [(t, q) for t in trades if (q := sizer.quantity(t.entry_price)) > 0]
        skipped = len(trades) - len(sized)
        if not sized:
            return ScreenResult(0, skipped, None, None, None, None, None, None, (), advisory)
        gross = [t.gross_return for t, _ in sized]
        net = [
            t.gross_return - self._costs.round_trip_fraction(t, q, self._benchmark)
            for t, q in sized
        ]
        adverse_cost = [self._costs.round_trip_fraction(t, q, self._adverse) for t, q in sized]
        result = ScreenResult(
            trades=len(sized),
            skipped_unaffordable=skipped,
            gross_mean=DecimalMath.mean(gross),
            net_mean=DecimalMath.mean(net),
            net_t=self._t_statistic(net),
            adverse_break_even=DecimalMath.mean(adverse_cost),
            positive_year_share=self._positive_year_share(
                [(t, n) for (t, _), n in zip(sized, net, strict=False)]
            ),
            top_instrument_share=self._top_instrument_share(
                [(t, n) for (t, _), n in zip(sized, net, strict=False)]
            ),
            checks=(),
            advisory=advisory,
        )
        return replace(result, checks=self._checks(result))

    def _checks(self, r: ScreenResult) -> tuple[ScreenCheck, ...]:
        assert (
            r.net_mean is not None and r.gross_mean is not None and r.adverse_break_even is not None
        )
        bar = self._bar
        return (
            ScreenCheck("net expectancy > 0", r.net_mean > _ZERO, f"{r.net_mean:.6f}"),
            ScreenCheck(
                "gross >= adverse break-even",
                r.gross_mean >= r.adverse_break_even,
                f"{r.gross_mean:.6f} vs {r.adverse_break_even:.6f}",
            ),
            ScreenCheck(
                f"net t >= {bar.min_net_t}",
                r.net_t is not None and r.net_t >= bar.min_net_t,
                "n/a" if r.net_t is None else f"{r.net_t:.3f}",
            ),
            ScreenCheck(f"trades >= {bar.min_trades}", r.trades >= bar.min_trades, str(r.trades)),
            ScreenCheck(
                f"net positive in >= {bar.min_positive_year_share} of years",
                r.positive_year_share is not None
                and r.positive_year_share >= bar.min_positive_year_share,
                str(r.positive_year_share),
            ),
            ScreenCheck(
                f"no instrument > {bar.max_top_instrument_share} of net",
                r.top_instrument_share is not None
                and r.top_instrument_share <= bar.max_top_instrument_share,
                str(r.top_instrument_share),
            ),
        )

    @staticmethod
    def _t_statistic(values: Sequence[Decimal]) -> Decimal | None:
        if len(values) < 2:
            return None
        stdev = DecimalMath.sample_stdev(values)
        if stdev == _ZERO:
            return None
        error = DecimalMath.divide(stdev, DecimalMath.sqrt(Decimal(len(values))))
        return DecimalMath.divide(DecimalMath.mean(values), error)

    @staticmethod
    def _positive_year_share(paired: Sequence[tuple[ScreenTrade, Decimal]]) -> Decimal:
        by_year: dict[int, Decimal] = defaultdict(Decimal)
        for trade, net in paired:
            by_year[trade.day.year] += net
        positive = sum(1 for total in by_year.values() if total > _ZERO)
        return DecimalMath.divide(Decimal(positive), Decimal(len(by_year)))

    @staticmethod
    def _top_instrument_share(paired: Sequence[tuple[ScreenTrade, Decimal]]) -> Decimal | None:
        """The biggest single instrument's net over the total net. None when nothing was earned,
        because there is no profit to concentrate."""
        by_instrument: dict[str, Decimal] = defaultdict(Decimal)
        for trade, net in paired:
            by_instrument[trade.instrument_id] += net
        total = sum(by_instrument.values(), _ZERO)
        if total <= _ZERO:
            return None
        return DecimalMath.divide(max(by_instrument.values()), total)
