"""Cells B1 and B1b: the NIFTY put credit spread ladder, as declared
(config/experiments/b1-nifty-put-spread-ladder.yaml, EM-230, and its child
b1b-nifty-put-spread-no-stop.yaml, EM-234, which drops the 2x stop and varies the wing cap).

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

__all__ = [
    "B1B_DESIGN",
    "B1_DESIGN",
    "DESIGNS",
    "B1Arms",
    "B1Backtests",
    "B1Inputs",
    "B1Parameters",
    "CellDesign",
    "warmup_end",
]

MAX_WING_LOSS = Decimal(5000)  # PROFIT_PLAN §3.5: 5% of Rs 1,00,000 per spread
MIN_DTE, MAX_DTE = 21, 49
TREND_WINDOW, FAST_WINDOW, VIX_LOOKBACK = 200, 50, 252
_GRID = ("k", "filter", "ladder_depth")
_SHORT = {"ladder_depth": "depth"}  # how an axis is named in an arm's label


@dataclass(frozen=True)
class CellDesign:
    """What a declared cell fixes and what it varies: the axes its grid must name exactly, the
    values it holds constant, and its exit stop (None: no stop). B1 and its child B1b differ only
    here, so one runner serves both."""

    slug: str
    axes: tuple[str, ...]
    fixed: tuple[tuple[str, str], ...]
    stop: Decimal | None
    closure_multiple: Decimal | None = (
        None  # declared closure test: zero-slip gross >= this x charges
    )


B1_DESIGN = CellDesign(
    "b1-nifty-put-spread-ladder", _GRID, (("wing_cap", str(MAX_WING_LOSS)),), Decimal(2)
)
B1B_DESIGN = CellDesign(
    "b1b-nifty-put-spread-no-stop",
    ("filter", "wing_cap"),
    (("k", "1.0"), ("ladder_depth", "2")),
    None,
    Decimal(2),
)
DESIGNS = (B1_DESIGN, B1B_DESIGN)


@dataclass(frozen=True)
class B1Parameters:
    k: Decimal
    regime_filter: bool
    ladder_depth: int
    wing_cap: Decimal = MAX_WING_LOSS
    design: CellDesign = B1_DESIGN

    def __post_init__(self) -> None:
        if self.k <= 0:
            raise ValueError("k must be positive")
        if self.ladder_depth not in (2, 3):
            raise ValueError("the declared ladder depths are 2 and 3")
        if self.wing_cap <= 0:
            raise ValueError("the wing cap must be positive")

    @classmethod
    def from_point(cls, point: Mapping[str, str], design: CellDesign = B1_DESIGN) -> B1Parameters:
        merged = {**dict(design.fixed), **point}
        switch = merged["filter"]
        if switch not in ("on", "off"):
            raise ValueError(f"filter must be on or off, not {switch!r}")
        return cls(
            Decimal(merged["k"]),
            switch == "on",
            int(merged["ladder_depth"]),
            Decimal(merged["wing_cap"]),
            design,
        )

    def as_point(self) -> dict[str, str]:
        """The arm's values on the axes its cell declares."""
        every = {
            "k": str(self.k),
            "filter": "on" if self.regime_filter else "off",
            "ladder_depth": str(self.ladder_depth),
            "wing_cap": str(self.wing_cap),
        }
        return {axis: every[axis] for axis in self.design.axes}

    @property
    def aggressive(self) -> bool:
        """An arm that needs the §3.4 ruin check: a third slot, or a wing beyond the §3.5 cap."""
        return self.ladder_depth == 3 or self.wing_cap > MAX_WING_LOSS

    @property
    def label(self) -> str:
        return " ".join(f"{_SHORT.get(a, a)}={v}" for a, v in self.as_point().items())


class B1Arms:
    @staticmethod
    def declared(grid: Mapping[str, Sequence[str]]) -> list[B1Parameters]:
        """Every arm of the declared grid. The declaration must name exactly this cell's axes."""
        design = next((d for d in DESIGNS if sorted(grid) == sorted(d.axes)), None)
        if design is None:
            known = "; ".join(f"{d.slug}: {list(d.axes)}" for d in DESIGNS)
            raise ValueError(f"the declared grid must name exactly a cell's axes ({known})")
        points = product(*(grid[name] for name in design.axes))
        return [
            B1Parameters.from_point(dict(zip(design.axes, p, strict=True)), design) for p in points
        ]

    @staticmethod
    def adjacent(arms: Sequence[B1Parameters]) -> dict[str, list[str]]:
        """For each arm (by label), the arms that differ from it in exactly one dimension."""
        out: dict[str, list[str]] = {}
        for arm in arms:
            mine = arm.as_point()
            out[arm.label] = [
                other.label
                for other in arms
                if sum(mine[a] != other.as_point()[a] for a in arm.design.axes) == 1
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
            RiskCappedPutSpread(VixVolatility(i.vix_closes), arm.k, arm.wing_cap),
        )
        return SpreadBacktester(
            i.chains, entry, standard_policy(stop=arm.design.stop), SpanLikeMargin(), i.fees,
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
