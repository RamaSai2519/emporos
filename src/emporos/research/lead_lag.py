"""Intraday market/sector lead-lag research (EM-180): does the first N minutes of a session's
move in a predictor (the market, or a sector) say anything about what a target (a stock, or a
sector) does next — continuation, or reversal?

`LeadLagEngine` is deliberately agnostic to what "predictor" and "target" represent: both arrive
as within-day return series (`emporos.research.sessions.session_local_returns`, or an
equal-weighted average of several — see that module's docstring), so the SAME engine evaluates
"NIFTY proxy -> one stock", "a sector -> its own members", or "a sector -> another sector" without
three code paths. Continuation vs reversal is not a separate mode: each day is sorted into an
"up" or "down" group by its predictor reading's sign, and `emporos.research.statistics.
ExpectancyStats` (unchanged, from EM-178) reports that group's conditional expectancy —
positive means continuation, negative means reversal, for whichever sign group it is.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.research.costs import TransactionCostModel
from emporos.research.engine import RegimeAxisFactory
from emporos.research.horizons import Horizon
from emporos.research.sessions import early_return, late_session_return, subsequent_return
from emporos.research.statistics import ExpectancyReport, ExpectancyStats, Observation

_ZERO = Decimal(0)
LATE_SESSION = "late_session"


@dataclass(frozen=True)
class LeadLagSegment:
    early_horizon: Horizon
    target_horizon_label: str  # a Horizon's label, or LATE_SESSION
    direction: str  # "up" or "down"
    axis: str | None
    label: str | None
    report: ExpectancyReport


class LeadLagEngine:
    """Pure, I/O-free, like `AlphaDiscoveryEngine`/`CrossSectionalEngine`: fetching and
    aligning the per-day series is the composition root's / `LeadLagStudy`'s job."""

    def __init__(
        self,
        early_horizons: Sequence[Horizon],
        target_horizons: Sequence[Horizon],
        cost_model: TransactionCostModel,
        exchange: Exchange,
        capital: Money,
        include_late_session: bool = True,
        conditioning_axes: Sequence[RegimeAxisFactory] = (),
    ) -> None:
        if not early_horizons:
            raise ValueError("at least one early horizon is required")
        if not target_horizons and not include_late_session:
            raise ValueError("at least one target horizon (or late-session) is required")
        if capital <= Money.zero():
            raise ValueError("capital must be positive")
        self._early_horizons = tuple(early_horizons)
        self._target_horizons = tuple(target_horizons)
        self._cost_model = cost_model
        self._exchange = exchange
        self._capital = capital
        self._include_late_session = include_late_session
        self._conditioning_axes = tuple(conditioning_axes)

    @property
    def cost_model_label(self) -> str:
        return self._cost_model.label

    def evaluate(
        self,
        predictor_by_day: Mapping[date, Sequence[Decimal]],
        target_by_day: Mapping[date, Sequence[Decimal]],
        regime_bars_by_day: Mapping[date, Sequence[Candle]],
        price_by_day: Mapping[date, Money] | None = None,
    ) -> list[LeadLagSegment]:
        days = sorted(set(predictor_by_day) & set(target_by_day) & set(regime_bars_by_day))
        segments: list[LeadLagSegment] = []
        for early_horizon in self._early_horizons:
            labels_by_day = self._labels_by_day(regime_bars_by_day, days, early_horizon)
            groups = self._group(
                days, early_horizon, predictor_by_day, target_by_day, price_by_day, labels_by_day
            )
            for (target_label, direction, axis, label), observations in groups.items():
                if not observations:
                    continue
                report = ExpectancyStats.of(observations)
                segments.append(
                    LeadLagSegment(early_horizon, target_label, direction, axis, label, report)
                )
        return segments

    def _labels_by_day(
        self,
        regime_bars_by_day: Mapping[date, Sequence[Candle]],
        days: Sequence[date],
        early_horizon: Horizon,
    ) -> dict[date, dict[str, str | None]]:
        axes = [factory() for factory in self._conditioning_axes]
        target_index = early_horizon.bars - 1
        labels: dict[date, dict[str, str | None]] = {}
        for day in days:
            day_label: dict[str, str | None] = {}
            for i, candle in enumerate(regime_bars_by_day[day]):
                current = {axis.name: axis.update(candle) for axis in axes}
                if i == target_index:
                    day_label = current
            labels[day] = day_label
        return labels

    def _group(
        self,
        days: Sequence[date],
        early_horizon: Horizon,
        predictor_by_day: Mapping[date, Sequence[Decimal]],
        target_by_day: Mapping[date, Sequence[Decimal]],
        price_by_day: Mapping[date, Money] | None,
        labels_by_day: dict[date, dict[str, str | None]],
    ) -> dict[tuple[str, str, str | None, str | None], list[Observation]]:
        groups: dict[tuple[str, str, str | None, str | None], list[Observation]] = defaultdict(
            list
        )
        for day in days:
            predictor = early_return(predictor_by_day[day], early_horizon)
            if predictor is None or predictor == _ZERO:
                continue
            direction = "up" if predictor > _ZERO else "down"
            signed_predictor = predictor if direction == "up" else -predictor
            for target_label, target_return in self._targets(target_by_day[day], early_horizon):
                if target_return is None:
                    continue
                net = self._net_return(target_return, day, price_by_day)
                observation = Observation(signed_predictor, target_return, net)
                groups[(target_label, direction, None, None)].append(observation)
                for axis_name, bucket in labels_by_day[day].items():
                    if bucket is not None:
                        groups[(target_label, direction, axis_name, bucket)].append(observation)
        return groups

    def _targets(
        self, target_returns: Sequence[Decimal], early_horizon: Horizon
    ) -> list[tuple[str, Decimal | None]]:
        results = [
            (h.label, subsequent_return(target_returns, early_horizon, h))
            for h in self._target_horizons
        ]
        if self._include_late_session:
            results.append((LATE_SESSION, late_session_return(target_returns, early_horizon)))
        return results

    def _net_return(
        self, gross: Decimal, day: date, price_by_day: Mapping[date, Money] | None
    ) -> Decimal:
        if price_by_day is None or day not in price_by_day:
            return gross  # no single tradable price (a sector aggregate): net == gross, by design
        price = price_by_day[day]
        if price.amount <= _ZERO:
            return gross
        quantity = int(self._capital.amount // price.amount)
        if quantity < 1:
            return gross
        return self._cost_model.adjusted_return(gross, self._exchange, quantity, price)
