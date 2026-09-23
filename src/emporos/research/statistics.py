"""Evaluation statistics for one feature at one horizon (EM-178): conditional expectancy, median
return, hit rate, rank IC, decile spread, a t-statistic and the sample size everything else is
conditioned on. Every ratio is exact `Decimal` arithmetic via `DecimalMath` (plan.md's "no floats
near money" extends to anything a validation decision is made from).

"Conditional expectancy" here means: conditioned on the feature signal being ON (`feature_value >
0`) — the mean of what happened next when the feature fired, which is what a strategy built on it
would actually earn. Rank IC and decile spread instead use every observation, fired or not, since
they measure the feature's ranking power across its whole range, not just above zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath

_ZERO = Decimal(0)
_DECILE_COUNT = 10


@dataclass(frozen=True)
class Observation:
    """One (feature value, what happened next) pair; `cost_adjusted_return` already has the
    round-trip cost assumption applied."""

    feature_value: Decimal
    forward_return: Decimal
    cost_adjusted_return: Decimal


class InsufficientSampleError(ValueError):
    """Too few observations for a statistic to mean anything."""


@dataclass(frozen=True)
class ExpectancyReport:
    sample_size: int
    triggered: int  # observations where the feature signal was ON (feature_value > 0)
    conditional_expectancy: Decimal | None  # mean forward return, triggered only
    cost_adjusted_expectancy: Decimal | None  # same, after the round-trip cost assumption
    median_return: Decimal | None  # triggered only
    hit_rate: Decimal | None  # triggered only: fraction with a positive forward return
    rank_ic: Decimal | None  # Spearman correlation, feature value vs forward return, ALL rows
    decile_spread: Decimal | None  # top decile mean return minus bottom decile's, ALL rows
    t_statistic: Decimal | None  # of the triggered group's mean forward return against zero


class ExpectancyStats:
    @staticmethod
    def of(observations: Sequence[Observation]) -> ExpectancyReport:
        if not observations:
            raise InsufficientSampleError("no observations")
        triggered = [o for o in observations if o.feature_value > _ZERO]
        returns = [o.forward_return for o in triggered]
        cost_returns = [o.cost_adjusted_return for o in triggered]
        return ExpectancyReport(
            sample_size=len(observations),
            triggered=len(triggered),
            conditional_expectancy=DecimalMath.mean(returns) if returns else None,
            cost_adjusted_expectancy=DecimalMath.mean(cost_returns) if cost_returns else None,
            median_return=ExpectancyStats._median(returns) if returns else None,
            hit_rate=ExpectancyStats._hit_rate(returns) if returns else None,
            rank_ic=(ExpectancyStats._rank_ic(observations) if len(observations) >= 2 else None),
            decile_spread=(
                ExpectancyStats._decile_spread(observations)
                if len(observations) >= _DECILE_COUNT
                else None
            ),
            t_statistic=ExpectancyStats._t_statistic(returns) if len(returns) >= 2 else None,
        )

    @staticmethod
    def _median(values: Sequence[Decimal]) -> Decimal:
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return DecimalMath.divide(ordered[mid - 1] + ordered[mid], Decimal(2))

    @staticmethod
    def _hit_rate(values: Sequence[Decimal]) -> Decimal:
        hits = sum(1 for v in values if v > _ZERO)
        return DecimalMath.divide(Decimal(hits), Decimal(len(values)))

    @staticmethod
    def _t_statistic(values: Sequence[Decimal]) -> Decimal | None:
        stdev = DecimalMath.sample_stdev(values)
        if stdev == _ZERO:
            return None
        mean = DecimalMath.mean(values)
        standard_error = DecimalMath.divide(stdev, DecimalMath.sqrt(Decimal(len(values))))
        return DecimalMath.divide(mean, standard_error)

    @staticmethod
    def _rank_ic(observations: Sequence[Observation]) -> Decimal | None:
        feature_ranks = ExpectancyStats._ranks([o.feature_value for o in observations])
        return_ranks = ExpectancyStats._ranks([o.forward_return for o in observations])
        if DecimalMath.sample_stdev(feature_ranks) == _ZERO:
            return None
        if DecimalMath.sample_stdev(return_ranks) == _ZERO:
            return None
        return ExpectancyStats._pearson(feature_ranks, return_ranks)

    @staticmethod
    def _decile_spread(observations: Sequence[Observation]) -> Decimal:
        ordered = sorted(observations, key=lambda o: o.feature_value)
        decile = max(1, len(ordered) // _DECILE_COUNT)
        bottom, top = ordered[:decile], ordered[-decile:]
        return DecimalMath.mean([o.forward_return for o in top]) - DecimalMath.mean(
            [o.forward_return for o in bottom]
        )

    @staticmethod
    def _ranks(values: Sequence[Decimal]) -> list[Decimal]:
        """Fractional (average) ranks, so tied values share one rank rather than an arbitrary
        tie-break deciding a correlation."""
        ordered = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [_ZERO] * len(values)
        i = 0
        while i < len(ordered):
            j = i
            while j + 1 < len(ordered) and values[ordered[j + 1]] == values[ordered[i]]:
                j += 1
            average_rank = DecimalMath.divide(Decimal(i + j + 2), Decimal(2))  # 1-based, averaged
            for k in range(i, j + 1):
                ranks[ordered[k]] = average_rank
            i = j + 1
        return ranks

    @staticmethod
    def _pearson(a: Sequence[Decimal], b: Sequence[Decimal]) -> Decimal:
        mean_a, mean_b = DecimalMath.mean(a), DecimalMath.mean(b)
        covariance = sum(((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True)), _ZERO)
        variance_a = sum(((x - mean_a) ** 2 for x in a), _ZERO)
        variance_b = sum(((y - mean_b) ** 2 for y in b), _ZERO)
        denominator = DecimalMath.sqrt(variance_a * variance_b)
        if denominator == _ZERO:
            return _ZERO
        return DecimalMath.divide(covariance, denominator)
