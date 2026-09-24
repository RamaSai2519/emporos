"""Cross-sectional residual momentum (EM-179): rank the universe by a trailing, market/sector-
neutralized return (`emporos.research.factors.ResidualSignalCalculator`), and test whether the
top and bottom tails keep moving (continuation) or give it back (reversal) over multiple forward
holding horizons. Long the top tail and short the bottom and the resulting spread portfolio is
market-neutral by construction (each leg's market/sector exposure was what the signal removed),
so "does the edge survive costs" is judged on a diversified portfolio's expected return, not one
stock's forecast — the same discipline `emporos.research.engine.AlphaDiscoveryEngine` applies to a
single feature, extended across the universe at each bar instead of down one instrument's history.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.sizing import DeclaredSize
from emporos.research.costs import TransactionCostModel
from emporos.research.engine import RegimeAxisFactory
from emporos.research.factors import (
    AlignedUniverse,
    BetaEstimator,
    MarketFactor,
    ResidualSignalCalculator,
    SectorFactor,
    bar_returns,
)
from emporos.research.horizons import ForwardReturnCalculator, Horizon
from emporos.research.regimes import SectorLookup
from emporos.research.statistics import InsufficientSampleError

_ZERO = Decimal(0)


class Tail(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class TailReport:
    sample_size: int
    gross_expectancy: Decimal | None
    net_expectancy: Decimal | None
    hit_rate: Decimal | None  # fraction of gross forward returns that were positive
    t_statistic: Decimal | None  # of the gross forward returns' mean against zero


class TailStats:
    """Every observation in a tail already earned its place there by ranking; unlike
    `emporos.research.statistics.ExpectancyStats`, nothing here filters further by sign."""

    @staticmethod
    def of(gross_returns: Sequence[Decimal], net_returns: Sequence[Decimal]) -> TailReport:
        if not gross_returns:
            raise InsufficientSampleError("no observations")
        return TailReport(
            sample_size=len(gross_returns),
            gross_expectancy=DecimalMath.mean(gross_returns),
            net_expectancy=DecimalMath.mean(net_returns),
            hit_rate=DecimalMath.divide(
                Decimal(sum(1 for r in gross_returns if r > _ZERO)), Decimal(len(gross_returns))
            ),
            t_statistic=TailStats._t_statistic(gross_returns),
        )

    @staticmethod
    def _t_statistic(values: Sequence[Decimal]) -> Decimal | None:
        if len(values) < 2:
            return None
        stdev = DecimalMath.sample_stdev(values)
        if stdev == _ZERO:
            return None
        mean = DecimalMath.mean(values)
        standard_error = DecimalMath.divide(stdev, DecimalMath.sqrt(Decimal(len(values))))
        return DecimalMath.divide(mean, standard_error)


@dataclass(frozen=True)
class TailSegment:
    """One slice of a study's results: pooled (`axis is None`) or one regime bucket, for one
    tail, one signal horizon and one holding horizon."""

    tail: Tail
    signal_horizon: Horizon
    holding_horizon: Horizon
    axis: str | None
    label: str | None
    report: TailReport


class CrossSectionalEngine:
    """Evaluates cross-sectional residual momentum over an already-aligned universe: pure,
    I/O-free, like `AlphaDiscoveryEngine`. Fetching aligned bars and recording results is the
    composition root's / `CrossSectionalStudy`'s job.

    Every leg trades at the declared `size`, so a book of `2 * tail` legs deploys `2 * tail * size`
    and that must fit `capital` (the platform's capital-deployed limit): a study whose tails cannot
    be held at the declared size is refused, not quietly shrunk (EM-191 F4)."""

    def __init__(
        self,
        signal_horizons: Sequence[Horizon],
        holding_returns: ForwardReturnCalculator,
        beta_estimator: BetaEstimator,
        cost_model: TransactionCostModel,
        exchange: Exchange,
        capital: Money,
        size: DeclaredSize,
        tail_fraction: Decimal = Decimal("0.2"),
        sectors: SectorLookup | None = None,
        conditioning_axes: Sequence[RegimeAxisFactory] = (),
    ) -> None:
        if not signal_horizons:
            raise ValueError("at least one signal horizon is required")
        if not (Decimal(0) < tail_fraction <= Decimal("0.5")):
            raise ValueError("tail_fraction must lie in (0, 0.5]")
        if capital <= Money.zero():
            raise ValueError("capital must be positive")
        self._signal_horizons = tuple(signal_horizons)
        self._holding_returns = holding_returns
        self._signal = ResidualSignalCalculator(beta_estimator)
        self._cost_model = cost_model
        self._exchange = exchange
        self._capital = capital
        self._size = size
        self._tail_fraction = tail_fraction
        self._sectors = sectors
        self._conditioning_axes = tuple(conditioning_axes)

    @property
    def size(self) -> DeclaredSize:
        return self._size

    def _require_deployable(self, tail_size: int) -> None:
        deployed = self._size.position_value * 2 * tail_size
        if deployed > self._capital.amount:
            raise ValueError(
                f"{2 * tail_size} legs at {self._size.position_value} each deploy {deployed}, "
                f"above the {self._capital.amount} capital: declare a smaller size or a smaller "
                "tail_fraction"
            )

    @property
    def cost_model_label(self) -> str:
        return self._cost_model.label

    def evaluate(self, universe: AlignedUniverse) -> list[TailSegment]:
        returns = {i: bar_returns(universe.bars_of(i)) for i in universe.instrument_ids}
        market = MarketFactor(universe)
        sector = SectorFactor(universe, self._sectors) if self._sectors is not None else None
        labels = self._conditioning_labels(universe)

        accumulator: dict[
            tuple[Tail, Horizon, Horizon, str | None, str | None], list[tuple[Decimal, Decimal]]
        ] = defaultdict(list)
        for signal_horizon in self._signal_horizons:
            for index in range(universe.length):
                self._evaluate_bar(
                    universe, index, signal_horizon, returns, market, sector, labels, accumulator
                )

        return [
            TailSegment(tail, signal, holding, axis, label, TailStats.of(*_split(pairs)))
            for (tail, signal, holding, axis, label), pairs in accumulator.items()
        ]  # fmt: skip

    def _conditioning_labels(
        self, universe: AlignedUniverse
    ) -> dict[str, dict[str, list[str | None]]]:
        """One fresh axis instance PER INSTRUMENT (never shared across instruments, per
        `AlphaDiscoveryEngine`'s same rule), fed causally bar by bar."""
        labels: dict[str, dict[str, list[str | None]]] = {}
        for instrument_id in universe.instrument_ids:
            axes = [factory() for factory in self._conditioning_axes]
            per_axis: dict[str, list[str | None]] = {axis.name: [] for axis in axes}
            for candle in universe.bars_of(instrument_id):
                for axis in axes:
                    per_axis[axis.name].append(axis.update(candle))
            labels[instrument_id] = per_axis
        return labels

    def _evaluate_bar(
        self,
        universe: AlignedUniverse,
        index: int,
        signal_horizon: Horizon,
        returns: dict[str, list[Decimal | None]],
        market: MarketFactor,
        sector: SectorFactor | None,
        labels: dict[str, dict[str, list[str | None]]],
        accumulator: dict[
            tuple[Tail, Horizon, Horizon, str | None, str | None], list[tuple[Decimal, Decimal]]
        ],
    ) -> None:
        signals: dict[str, Decimal] = {}
        for instrument_id in universe.instrument_ids:
            sector_returns = sector.returns_for(instrument_id) if sector is not None else None
            value = self._signal.signal_at(
                index, signal_horizon, returns[instrument_id], market.returns, sector_returns
            )
            if value is not None:
                signals[instrument_id] = value
        tail_size = max(1, int(len(signals) * self._tail_fraction))
        if len(signals) < 2 or tail_size * 2 > len(signals):
            return
        ranked = sorted(signals.items(), key=lambda kv: kv[1], reverse=True)
        top, bottom = ranked[:tail_size], ranked[-tail_size:]
        self._require_deployable(tail_size)

        for tail, members in ((Tail.TOP, top), (Tail.BOTTOM, bottom)):
            for instrument_id, _ in members:
                self._record_member(
                    universe, instrument_id, index, tail, signal_horizon,
                    labels, accumulator,
                )  # fmt: skip

    def _record_member(
        self,
        universe: AlignedUniverse,
        instrument_id: str,
        index: int,
        tail: Tail,
        signal_horizon: Horizon,
        labels: dict[str, dict[str, list[str | None]]],
        accumulator: dict[
            tuple[Tail, Horizon, Horizon, str | None, str | None], list[tuple[Decimal, Decimal]]
        ],
    ) -> None:
        bars = universe.bars_of(instrument_id)
        price = bars[index].close
        if price.amount <= _ZERO:
            return
        quantity = self._size.quantity_at(price)
        if quantity < 1:
            return
        for holding_horizon, gross in self._holding_returns.returns_at(bars, index).items():
            if gross is None:
                continue
            net = self._cost_model.adjusted_return(gross, self._exchange, quantity, price)
            key = (tail, signal_horizon, holding_horizon, None, None)
            accumulator[key].append((gross, net))
            for axis_name, per_bar in labels[instrument_id].items():
                label = per_bar[index]
                if label is not None:
                    accumulator[(tail, signal_horizon, holding_horizon, axis_name, label)].append(
                        (gross, net)
                    )


def _split(pairs: Sequence[tuple[Decimal, Decimal]]) -> tuple[list[Decimal], list[Decimal]]:
    return [g for g, _ in pairs], [n for _, n in pairs]
