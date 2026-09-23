"""Ties a feature, forward returns, cost adjustment and regime segmentation together into one
evaluation (EM-178) — the acceptance criteria's core loop, over bars already in memory. Fetching
those bars and recording the result is `emporos.research.study`'s job, not this class's: this one
stays pure and I/O-free so it is trivially unit-testable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from emporos.domain.candles import Candle
from emporos.domain.instruments import Exchange
from emporos.research.costs import TransactionCostModel
from emporos.research.features import Feature, FeatureSeries
from emporos.research.horizons import ForwardReturnCalculator, Horizon
from emporos.research.regimes import RegimeAxis
from emporos.research.statistics import ExpectancyReport, ExpectancyStats, Observation

RegimeAxisFactory = Callable[[], RegimeAxis]


@dataclass(frozen=True)
class Segment:
    """One slice of a study's results: pooled (`axis is None`) or one regime bucket."""

    axis: str | None
    label: str | None
    horizon: Horizon
    report: ExpectancyReport


class AlphaDiscoveryEngine:
    """Evaluates one `Feature` over one instrument's bars: pooled, and broken down by every
    injected regime axis. `regime_axes` takes FACTORIES, not instances: each axis carries its own
    rolling history, so a fresh one is built per `evaluate` call — reusing one across instruments
    would leak one instrument's volatility/liquidity history into another's classification."""

    def __init__(
        self,
        forward_returns: ForwardReturnCalculator,
        cost_model: TransactionCostModel,
        exchange: Exchange,
        quantity: int,
        regime_axes: Sequence[RegimeAxisFactory] = (),
    ) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._forward_returns = forward_returns
        self._cost_model = cost_model
        self._exchange = exchange
        self._quantity = quantity
        self._regime_axes = tuple(regime_axes)

    @property
    def cost_model_label(self) -> str:
        return self._cost_model.label

    def evaluate(self, feature: Feature, bars: Sequence[Candle]) -> list[Segment]:
        values = FeatureSeries(feature).compute(bars)
        per_horizon: dict[Horizon, list[tuple[int, Observation]]] = {
            horizon: [] for horizon in self._forward_returns.horizons
        }
        for index, value in enumerate(values):
            if value is None:
                continue
            for horizon, forward in self._forward_returns.returns_at(bars, index).items():
                if forward is None:
                    continue
                adjusted = self._cost_model.adjusted_return(
                    forward, self._exchange, self._quantity, bars[index].close
                )
                per_horizon[horizon].append((index, Observation(value, forward, adjusted)))

        segments = [
            Segment(None, None, horizon, ExpectancyStats.of([o for _, o in indexed]))
            for horizon, indexed in per_horizon.items()
            if indexed
        ]
        for factory in self._regime_axes:
            axis = factory()
            labels = [axis.update(bar) for bar in bars]
            for horizon, indexed in per_horizon.items():
                groups: dict[str, list[Observation]] = defaultdict(list)
                for index, observation in indexed:
                    label = labels[index]
                    if label is not None:
                        groups[label].append(observation)
                segments.extend(
                    Segment(axis.name, bucket, horizon, ExpectancyStats.of(subset))
                    for bucket, subset in groups.items()
                )
        return segments
