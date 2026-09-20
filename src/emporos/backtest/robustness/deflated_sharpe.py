"""Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014): is this Sharpe more than the luck of
having tried many things?

The best of N trials looks good even when none has an edge, so a Sharpe ratio is judged against
the Sharpe that the best of N skill-less trials would reach by chance (`expected_max_sharpe`, from
how widely the trials' Sharpes were spread), and the observed Sharpe is corrected for the skew and
fat tails of the returns and for how few days there were. The result is a probability: the chance
that the true Sharpe is above what selection alone would produce. Near 1 is convincing; 0.5 or
less is what luck looks like. The same formula with no selection (`expected_max_sharpe` = 0) is the
Probabilistic Sharpe Ratio, reported alongside so the effect of the trial count is visible.

Everything is per day (not annualised), as the paper's formula requires. The normal distribution's
CDF and inverse need a function `Decimal` does not have, so they are computed in float and returned
rounded to `PRECISION` places: they are probabilities, never money. The moments and the Sharpe
itself stay in `Decimal` under the metrics' fixed context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from statistics import NormalDist

from emporos.backtest.metrics.decimal_math import CONTEXT, ONE, ZERO, DecimalMath
from emporos.backtest.robustness.trials import TrialStatistics

PRECISION = Decimal("0.000000000001")
MIN_OBSERVATIONS = 10
EULER_MASCHERONI = Decimal("0.5772156649015328606065120900824024")
_E = Decimal(1).exp()
_FOUR = Decimal(4)
_NORMAL = NormalDist()


class NormalCurve:
    """The standard normal distribution at the float boundary, with `Decimal` on both sides."""

    @staticmethod
    def cdf(x: Decimal) -> Decimal:
        return Decimal(_NORMAL.cdf(float(x))).quantize(PRECISION)

    @staticmethod
    def inverse_cdf(p: Decimal) -> Decimal:
        if not ZERO < p < ONE:
            raise ValueError("the inverse normal needs a probability strictly between 0 and 1")
        return Decimal(_NORMAL.inv_cdf(float(p))).quantize(PRECISION)


@dataclass(frozen=True)
class ReturnMoments:
    observations: int
    mean: Decimal
    sharpe: Decimal  # per day: mean over sample standard deviation
    skewness: Decimal
    kurtosis: Decimal  # NOT excess: a normal distribution is 3

    @classmethod
    def of(cls, returns: Sequence[Decimal]) -> ReturnMoments | None:
        """None when there is no variance to divide by."""
        with localcontext(CONTEXT):
            return cls._of(returns)

    @classmethod
    def _of(cls, returns: Sequence[Decimal]) -> ReturnMoments | None:
        n = len(returns)
        mean = DecimalMath.mean(returns)
        sample_sd = DecimalMath.sample_stdev(returns)
        if sample_sd == ZERO:
            return None
        deviations = [r - mean for r in returns]
        population_sd = DecimalMath.sqrt(
            DecimalMath.divide(sum((d * d for d in deviations), ZERO), Decimal(n))
        )
        z = [DecimalMath.divide(d, population_sd) for d in deviations]
        return cls(
            n,
            mean,
            DecimalMath.divide(mean, sample_sd),
            DecimalMath.mean([v**3 for v in z]),
            DecimalMath.mean([v**4 for v in z]),
        )


@dataclass(frozen=True)
class DeflatedSharpeReport:
    observations: int
    trial_count: int
    daily_sharpe: Decimal | None
    annualised_sharpe: Decimal | None
    expected_max_sharpe: Decimal | None  # what the best of the trials would reach by luck
    probabilistic_sharpe: Decimal | None  # confidence that Sharpe > 0, ignoring the trial count
    deflated_sharpe: Decimal | None  # confidence that Sharpe > the luck benchmark
    reason: str | None  # why the figures are missing, when they are

    @property
    def computed(self) -> bool:
        return self.deflated_sharpe is not None


class LuckBenchmark:
    """The Sharpe the best of `count` skill-less trials would reach: `spread` is the standard
    deviation of the trials' Sharpes, and the two normal quantiles are the paper's approximation
    of the expected maximum of `count` draws."""

    @staticmethod
    def of(count: int, spread: Decimal) -> Decimal:
        if count < 1:
            raise ValueError("the benchmark needs at least one trial")
        if count == 1:
            return ZERO  # one look cannot have selected anything
        with localcontext(CONTEXT):
            n = Decimal(count)
            first = NormalCurve.inverse_cdf(ONE - ONE / n)
            second = NormalCurve.inverse_cdf(ONE - ONE / (n * _E))
            return spread * ((ONE - EULER_MASCHERONI) * first + EULER_MASCHERONI * second)


class SharpeConfidence:
    """The chance the true Sharpe exceeds `benchmark`, given the returns' skew, tails and length."""

    @staticmethod
    def of(moments: ReturnMoments, benchmark: Decimal) -> Decimal | None:
        """None when the Sharpe's own variance term is not positive, which real returns never
        give (kurtosis is at least skewness squared plus one) but which is refused, not assumed."""
        with localcontext(CONTEXT):
            sharpe = moments.sharpe
            term = ONE - moments.skewness * sharpe + (moments.kurtosis - ONE) / _FOUR * sharpe**2
            if term <= ZERO:
                return None
            scale = Decimal(moments.observations - 1).sqrt() / term.sqrt()
            return NormalCurve.cdf((sharpe - benchmark) * scale)


class DeflatedSharpe:
    def __init__(self, annualisation_days: int = 252) -> None:
        if annualisation_days <= 0:
            raise ValueError("annualisation days must be positive")
        self._root_days = DecimalMath.sqrt(Decimal(annualisation_days))

    def evaluate(
        self, daily_returns: Sequence[Decimal], trials: TrialStatistics
    ) -> DeflatedSharpeReport:
        n = len(daily_returns)
        if n < MIN_OBSERVATIONS:
            reason = f"{n} daily returns is fewer than the {MIN_OBSERVATIONS} needed"
            return self._missing(n, trials, reason)
        moments = ReturnMoments.of(daily_returns)
        if moments is None:
            return self._missing(n, trials, "the returns do not vary, so there is no Sharpe ratio")
        with localcontext(CONTEXT):
            annualised = moments.sharpe * self._root_days
        spread = self._trial_spread(trials)
        if isinstance(spread, str):
            return self._missing(n, trials, spread, moments.sharpe, annualised)
        benchmark = LuckBenchmark.of(trials.count, spread)
        psr = SharpeConfidence.of(moments, ZERO)
        dsr = SharpeConfidence.of(moments, benchmark)
        if psr is None or dsr is None:
            reason = "the skew and kurtosis leave the Sharpe's variance non-positive"
            return self._missing(n, trials, reason, moments.sharpe, annualised)
        return DeflatedSharpeReport(
            n, trials.count, moments.sharpe, annualised, benchmark, psr, dsr, None
        )

    @staticmethod
    def _trial_spread(trials: TrialStatistics) -> Decimal | str:
        """The standard deviation of the trials' Sharpes, or why it cannot be known."""
        if trials.count < 1:
            return "no trials are recorded, so the search size is unknown"
        if trials.count == 1:
            return ZERO
        if trials.sharpe_variance is None:
            return (
                f"{trials.count} trials but only {trials.scored} carry a Sharpe ratio: "
                "their spread, which sets the luck benchmark, cannot be measured"
            )
        return DecimalMath.sqrt(trials.sharpe_variance)

    @staticmethod
    def _missing(
        n: int,
        trials: TrialStatistics,
        reason: str,
        daily: Decimal | None = None,
        annualised: Decimal | None = None,
    ) -> DeflatedSharpeReport:
        return DeflatedSharpeReport(n, trials.count, daily, annualised, None, None, None, reason)
