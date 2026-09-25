"""Cell B1: the NIFTY put credit spread ladder, as declared
(config/experiments/b1-nifty-put-spread-ladder.yaml, EM-230).

`B1Parameters` is one arm: the strike distance `k`, the regime filter on or off, and the ladder
depth (2 normal, 3 aggressive, the third slot only on the conviction rule). `B1Arms` builds the grid
and says which arms are ADJACENT (differ in exactly one dimension), for the neighbour check.
`B1Backtests` wires one arm and one cost scenario into the option backtester from injected inputs;
nothing here reads a file, a database or the network."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import product

from emporos.options.backtest import BacktestSettings, SpreadBacktester
from emporos.options.chain_source import ChainSource
from emporos.options.depth import DepthPolicy, FixedDepth
from emporos.options.entry import EntryFilter, MonthlyExpiries, SpreadEntry
from emporos.options.exits import standard_policy
from emporos.options.fo_costs import FoFeeSchedule
from emporos.options.listed_strikes import RiskCappedPutSpread
from emporos.options.margin import SpanLikeMargin
from emporos.options.regime import (
    AllOf,
    ConditionFilter,
    ConvictionDepth,
    FirstSessionOfWeek,
    NoEventThroughExpiry,
    SmaStack,
    TrailingPercentileBand,
    TrendAboveSma,
    VixVolatility,
)
from emporos.options.slippage import SlippageScenario

__all__ = ["B1Arms", "B1Backtests", "B1Inputs", "B1Parameters", "warmup_end"]

MAX_WING_LOSS = Decimal(5000)  # PROFIT_PLAN §3.5: 5% of Rs 1,00,000 per spread
MIN_DTE, MAX_DTE = 21, 49
TREND_WINDOW, FAST_WINDOW, VIX_LOOKBACK = 200, 50, 252
_GRID = ("k", "filter", "ladder_depth")


@dataclass(frozen=True)
class B1Parameters:
    k: Decimal
    regime_filter: bool
    ladder_depth: int

    def __post_init__(self) -> None:
        if self.k <= 0:
            raise ValueError("k must be positive")
        if self.ladder_depth not in (2, 3):
            raise ValueError("the declared ladder depths are 2 and 3")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> B1Parameters:
        switch = point["filter"]
        if switch not in ("on", "off"):
            raise ValueError(f"filter must be on or off, not {switch!r}")
        return cls(Decimal(point["k"]), switch == "on", int(point["ladder_depth"]))

    def as_point(self) -> dict[str, str]:
        return {
            "k": str(self.k),
            "filter": "on" if self.regime_filter else "off",
            "ladder_depth": str(self.ladder_depth),
        }

    @property
    def aggressive(self) -> bool:
        return self.ladder_depth == 3

    @property
    def label(self) -> str:
        p = self.as_point()
        return f"k={p['k']} filter={p['filter']} depth={p['ladder_depth']}"


class B1Arms:
    @staticmethod
    def declared(grid: Mapping[str, Sequence[str]]) -> list[B1Parameters]:
        """Every arm of the declared grid. The declaration must name exactly this cell's axes."""
        if sorted(grid) != sorted(_GRID):
            raise ValueError(
                f"the declared grid must name exactly {list(_GRID)}, not {sorted(grid)}"
            )
        points = product(*(grid[name] for name in _GRID))
        return [B1Parameters.from_point(dict(zip(_GRID, p, strict=True))) for p in points]

    @staticmethod
    def adjacent(arms: Sequence[B1Parameters]) -> dict[str, list[str]]:
        """For each arm (by label), the arms that differ from it in exactly one dimension."""
        out: dict[str, list[str]] = {}
        for arm in arms:
            mine = arm.as_point()
            out[arm.label] = [
                other.label
                for other in arms
                if sum(mine[a] != other.as_point()[a] for a in _GRID) == 1
            ]
        return out


@dataclass(frozen=True)
class B1Inputs:
    chains: ChainSource
    nifty_closes: Mapping[date, Decimal]
    vix_closes: Mapping[date, Decimal]
    blackout_days: frozenset[date]
    monthly_expiries: Mapping[date, frozenset[date]]
    fees: FoFeeSchedule
    capital: Decimal


def warmup_end(nifty_closes: Mapping[date, Decimal], vix_closes: Mapping[date, Decimal]) -> date:
    """The first session on which every declared rule has its history: a full 200-session NIFTY
    average and 252 earlier VIX closes. Every arm is judged from this session on, so a filter-off
    arm is not credited with months the filter-on arms could not trade."""
    nifty, vix = sorted(nifty_closes), sorted(vix_closes)
    if len(nifty) < TREND_WINDOW or len(vix) <= VIX_LOOKBACK:
        raise ValueError("not enough NIFTY or India VIX history for the declared warm-up")
    return max(nifty[TREND_WINDOW - 1], vix[VIX_LOOKBACK])


class B1Backtests:
    def __init__(self, inputs: B1Inputs) -> None:
        self._in = inputs
        self._sessions = list(inputs.chains.days())

    def build(self, arm: B1Parameters, scenario: SlippageScenario) -> SpreadBacktester:
        i = self._in
        filters: list[EntryFilter] = [FirstSessionOfWeek(self._sessions)]
        if arm.regime_filter:
            band = TrailingPercentileBand(
                i.vix_closes, Decimal("0.2"), Decimal("0.8"), VIX_LOOKBACK
            )
            trend = TrendAboveSma(i.nifty_closes, TREND_WINDOW)
            filters += [
                ConditionFilter(AllOf((trend, band))),
                NoEventThroughExpiry(i.blackout_days),
            ]
        entry = SpreadEntry(
            filters,
            MonthlyExpiries(i.monthly_expiries, MIN_DTE, MAX_DTE),
            RiskCappedPutSpread(VixVolatility(i.vix_closes), arm.k, MAX_WING_LOSS),
        )
        return SpreadBacktester(
            i.chains, entry, standard_policy(), SpanLikeMargin(), i.fees,
            BacktestSettings(i.capital, 1, scenario), self._depth(arm),
        )  # fmt: skip

    def _depth(self, arm: B1Parameters) -> DepthPolicy:
        if not arm.aggressive:
            return FixedDepth(2)
        conviction = AllOf(
            (
                TrailingPercentileBand(
                    self._in.vix_closes, Decimal("0.5"), Decimal("0.8"), VIX_LOOKBACK
                ),
                SmaStack(self._in.nifty_closes, FAST_WINDOW, TREND_WINDOW),
            )
        )
        return ConvictionDepth(2, 1, conviction)
