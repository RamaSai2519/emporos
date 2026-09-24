"""The Deflated Sharpe Ratio against an independent float computation of the paper's formula, and
the properties that make it mean something: more trials or a wider spread deflate; one does not."""

from decimal import Decimal, localcontext

import pytest

from emporos.backtest.robustness.deflated_sharpe import (
    MIN_OBSERVATIONS,
    TRIAL_SPREADS,
    DeflatedSharpe,
    LuckBenchmark,
    NormalCurve,
    NullTrialSpread,
    ObservedTrialSpread,
    ReturnMoments,
    SharpeConfidence,
)
from emporos.backtest.robustness.trials import TrialStatistics

D = Decimal
RETURNS = [D(x) for x in "0.01 -0.01 0.02 0.00 0.01 -0.02 0.03 0.00 0.01 -0.01".split()]


def stats(count: int, variance: str | None) -> TrialStatistics:
    return TrialStatistics(count, count if variance else 0, D(variance) if variance else None)


def close(actual: Decimal | None, expected: str, places: str = "0.000001") -> None:
    assert actual is not None
    assert abs(actual - D(expected)) < D(places), (actual, expected)


class TestNormalCurve:
    def test_matches_the_familiar_values(self) -> None:
        close(NormalCurve.cdf(D("1.96")), "0.9750021049")
        close(NormalCurve.inverse_cdf(D("0.975")), "1.9599639845")
        assert NormalCurve.cdf(D(0)) == D("0.5")

    def test_inverse_refuses_a_probability_outside_the_open_interval(self) -> None:
        for bad in (D(0), D(1), D(-1)):
            with pytest.raises(ValueError):
                NormalCurve.inverse_cdf(bad)


class TestReturnMoments:
    def test_sharpe_skew_and_kurtosis_of_a_worked_series(self) -> None:
        moments = ReturnMoments.of(RETURNS)

        assert moments is not None
        close(moments.mean, "0.004", "1e-12")
        close(moments.sharpe, "0.2656844656620286", "1e-9")
        close(moments.skewness, "0.09884330004903571", "1e-9")
        close(moments.kurtosis, "2.247981545559399", "1e-9")

    def test_a_flat_series_has_no_sharpe(self) -> None:
        assert ReturnMoments.of([D("0.01")] * 12) is None


class TestDeflation:
    def test_matches_the_formula_with_ten_trials(self) -> None:
        report = DeflatedSharpe().evaluate(RETURNS, stats(10, "0.01"))

        close(report.expected_max_sharpe, "0.157459830134575", "1e-9")
        close(report.probabilistic_sharpe, "0.7877816420911072", "1e-9")
        close(report.deflated_sharpe, "0.6275472284228147", "1e-9")
        close(report.daily_sharpe, "0.2656844656620286", "1e-9")

    def test_a_narrower_spread_of_trial_sharpes_deflates_less(self) -> None:
        report = DeflatedSharpe().evaluate(RETURNS, stats(10, "0.0025"))

        close(report.expected_max_sharpe, "0.0787299150672875", "1e-9")
        close(report.deflated_sharpe, "0.7129610304377922", "1e-9")

    def test_more_trials_deflate_more(self) -> None:
        few = DeflatedSharpe().evaluate(RETURNS, stats(10, "0.0025"))
        many = DeflatedSharpe().evaluate(RETURNS, stats(100, "0.0025"))

        close(many.deflated_sharpe, "0.6621545175036264", "1e-9")
        assert many.deflated_sharpe < few.deflated_sharpe  # type: ignore[operator]
        assert many.expected_max_sharpe > few.expected_max_sharpe  # type: ignore[operator]

    def test_one_trial_is_the_probabilistic_sharpe_ratio(self) -> None:
        report = DeflatedSharpe().evaluate(RETURNS, TrialStatistics(1, 1, None))

        assert report.expected_max_sharpe == D(0)
        assert report.deflated_sharpe == report.probabilistic_sharpe

    def test_annualises_the_daily_sharpe(self) -> None:
        report = DeflatedSharpe(annualisation_days=252).evaluate(RETURNS, stats(10, "0.01"))

        close(report.annualised_sharpe, str(0.2656844656620286 * 252**0.5), "1e-8")

    def test_the_answer_does_not_depend_on_the_callers_decimal_context(self) -> None:
        normal = DeflatedSharpe().evaluate(RETURNS, stats(10, "0.01"))
        with localcontext() as ctx:
            ctx.prec = 6
            hostile = DeflatedSharpe().evaluate(RETURNS, stats(10, "0.01"))

        assert hostile == normal


class TestWhenItCannotBeComputed:
    def test_too_few_returns(self) -> None:
        report = DeflatedSharpe().evaluate(RETURNS[: MIN_OBSERVATIONS - 1], stats(10, "0.01"))

        assert not report.computed
        assert report.reason is not None and "fewer than" in report.reason

    def test_returns_that_never_vary(self) -> None:
        report = DeflatedSharpe().evaluate([D("0.01")] * 12, stats(10, "0.01"))

        assert not report.computed and report.reason is not None and "vary" in report.reason

    def test_no_trials_recorded(self) -> None:
        report = DeflatedSharpe().evaluate(RETURNS, TrialStatistics(0, 0, None))

        assert not report.computed and report.reason is not None and "no trials" in report.reason

    def test_many_trials_but_no_measurable_spread_says_so_rather_than_guessing(self) -> None:
        report = DeflatedSharpe().evaluate(RETURNS, TrialStatistics(35, 0, None))

        assert not report.computed
        assert report.reason is not None and "35 trials but only 0" in report.reason
        assert report.daily_sharpe is not None  # what could be computed still is

    def test_a_nonpositive_variance_term_is_refused_not_assumed(self) -> None:
        # not reachable from real returns (kurtosis >= skewness^2 + 1), so built directly
        impossible = ReturnMoments(20, D("0.01"), D(1), D(3), D(1))

        assert SharpeConfidence.of(impossible, D(0)) is None


class TestNullTrialSpread:
    """EM-206: the luck benchmark scaled by a skill-less Sharpe's scatter over the record."""

    def test_the_spread_is_one_over_root_t_minus_one_and_needs_no_trial_sharpes(self) -> None:
        moments = ReturnMoments.of(RETURNS)
        assert moments is not None

        spread = NullTrialSpread().of(TrialStatistics(35, 0, None), moments)

        close(spread, str(1 / 9**0.5), "1e-12")  # type: ignore[arg-type]

    def test_the_benchmark_is_the_expected_maximum_z_over_root_t_minus_one(self) -> None:
        null = DeflatedSharpe(spread=NullTrialSpread())

        report = null.evaluate(RETURNS, TrialStatistics(10, 0, None))

        close(report.expected_max_sharpe, str(1.57459830134575 / 3), "1e-9")
        assert report.computed

    def test_a_longer_record_lowers_the_benchmark_but_more_trials_still_raise_it(self) -> None:
        null = DeflatedSharpe(spread=NullTrialSpread())
        short = null.evaluate(RETURNS, TrialStatistics(100, 0, None))
        long = null.evaluate(RETURNS * 4, TrialStatistics(100, 0, None))
        wider = null.evaluate(RETURNS * 4, TrialStatistics(10_000, 0, None))

        assert long.expected_max_sharpe < short.expected_max_sharpe  # type: ignore[operator]
        assert wider.expected_max_sharpe > long.expected_max_sharpe  # type: ignore[operator]

    def test_no_trials_recorded_is_still_refused(self) -> None:
        report = DeflatedSharpe(spread=NullTrialSpread()).evaluate(
            RETURNS, TrialStatistics(0, 0, None)
        )

        assert not report.computed and report.reason is not None and "no trials" in report.reason


def test_the_default_spread_is_the_observed_one_and_both_are_named() -> None:
    by_default = DeflatedSharpe().evaluate(RETURNS, stats(10, "0.01"))
    observed = DeflatedSharpe(spread=ObservedTrialSpread()).evaluate(RETURNS, stats(10, "0.01"))

    assert by_default == observed
    assert set(TRIAL_SPREADS) == {"observed", "null_hypothesis"}


class TestLuckBenchmark:
    def test_the_expected_maximum_of_ten_standard_normals(self) -> None:
        close(LuckBenchmark.of(10, D(1)), "1.57459830134575", "1e-9")  # ~1.54 exact; paper's form

    def test_scales_with_the_spread_and_is_zero_for_one_trial(self) -> None:
        assert LuckBenchmark.of(1, D(5)) == D(0)
        close(LuckBenchmark.of(10, D("0.1")), "0.157459830134575", "1e-9")

    def test_refuses_no_trials(self) -> None:
        with pytest.raises(ValueError):
            LuckBenchmark.of(0, D(1))


def test_refuses_nonsense_annualisation() -> None:
    with pytest.raises(ValueError):
        DeflatedSharpe(annualisation_days=0)
