"""A paired, day-level bootstrap of the difference between two arms of an experiment (EM-187).

Two runs over the same days are not two independent samples: a good day for one is usually a good
day for the other, so comparing two intervals throws away exactly the pairing that makes a small
difference detectable. This resamples DAYS with replacement (the same days for both arms) and
reads the difference off each resample, giving an interval for "treatment minus baseline" that
answers the only question that matters: is the improvement distinguishable from noise?

It reuses the seeded `RandomSource` and nearest-rank `Percentiles` of the trade-level Monte Carlo
rather than adding new resampling maths, so the same seed gives the same interval on every
machine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ONE, ZERO, DecimalMath
from emporos.backtest.robustness.monte_carlo import (
    DEFAULT_RESAMPLES,
    Interval,
    Percentiles,
    RandomSource,
    SeededSource,
)

MIN_PAIRED_DAYS = 20


@dataclass(frozen=True)
class PairedBootstrapReport:
    days: int
    resamples: int
    seed: int
    confidence: Decimal
    mean_daily_return: Interval  # treatment minus baseline, per day
    daily_sharpe: Interval | None  # treatment minus baseline, per day (not annualised)
    undefined_sharpe_resamples: int  # resamples where an arm had no variance to divide by


class PairedDayBootstrap:
    def __init__(
        self,
        seed: int,
        resamples: int = DEFAULT_RESAMPLES,
        confidence: Decimal = Decimal("0.95"),
        min_days: int = MIN_PAIRED_DAYS,
    ) -> None:
        if resamples < 1:
            raise ValueError("resamples must be at least 1")
        if not ZERO < confidence < ONE:
            raise ValueError("confidence must be strictly between 0 and 1")
        if min_days < 2:
            raise ValueError("a paired bootstrap needs at least two days")
        self._seed = seed
        self._resamples = resamples
        self._confidence = confidence
        self._min_days = min_days

    def compare(
        self, baseline: Sequence[Decimal], treatment: Sequence[Decimal]
    ) -> PairedBootstrapReport | str:
        """The report, or the reason there is none: the arms must share their days, and there must
        be enough of them for a resampled interval to mean anything."""
        if len(baseline) != len(treatment):
            return (
                f"the arms do not share their days ({len(baseline)} vs {len(treatment)}), "
                "so they cannot be paired"
            )
        if len(baseline) < self._min_days:
            return (
                f"{len(baseline)} paired days is fewer than the {self._min_days} needed for "
                "a resampled interval to mean anything"
            )
        return self._resample(baseline, treatment, SeededSource(self._seed))

    def _resample(
        self, baseline: Sequence[Decimal], treatment: Sequence[Decimal], source: RandomSource
    ) -> PairedBootstrapReport:
        n = len(baseline)
        mean_deltas: list[Decimal] = []
        sharpe_deltas: list[Decimal] = []
        for _ in range(self._resamples):
            picks = [source.below(n) for _ in range(n)]
            base = [baseline[i] for i in picks]
            treat = [treatment[i] for i in picks]
            mean_deltas.append(DecimalMath.mean(treat) - DecimalMath.mean(base))
            delta = self._sharpe(treat), self._sharpe(base)
            if delta[0] is not None and delta[1] is not None:
                sharpe_deltas.append(delta[0] - delta[1])
        observed_sharpe = (self._sharpe(list(treatment)), self._sharpe(list(baseline)))
        sharpe = None
        if sharpe_deltas:
            observed = (
                observed_sharpe[0] - observed_sharpe[1]
                if observed_sharpe[0] is not None and observed_sharpe[1] is not None
                else None
            )
            sharpe = Percentiles.interval(observed, sharpe_deltas, self._confidence)
        return PairedBootstrapReport(
            days=n,
            resamples=self._resamples,
            seed=self._seed,
            confidence=self._confidence,
            mean_daily_return=Percentiles.interval(
                DecimalMath.mean(treatment) - DecimalMath.mean(baseline),
                mean_deltas,
                self._confidence,
            ),
            daily_sharpe=sharpe,
            undefined_sharpe_resamples=self._resamples - len(sharpe_deltas),
        )

    @staticmethod
    def _sharpe(returns: Sequence[Decimal]) -> Decimal | None:
        spread = DecimalMath.sample_stdev(returns)
        if spread == ZERO:
            return None
        return DecimalMath.divide(DecimalMath.mean(returns), spread)
