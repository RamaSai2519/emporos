"""EM-178: `ExpectancyStats` — hand-computed against small, known datasets."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.research.statistics import ExpectancyStats, InsufficientSampleError, Observation


def observation(
    feature_value: int, forward_return: str, cost_adjusted: str | None = None
) -> Observation:
    adjusted = Decimal(cost_adjusted) if cost_adjusted is not None else Decimal(forward_return)
    return Observation(Decimal(feature_value), Decimal(forward_return), adjusted)


def test_of_raises_on_an_empty_sample() -> None:
    with pytest.raises(InsufficientSampleError):
        ExpectancyStats.of([])


def test_sample_size_counts_every_observation_triggered_or_not() -> None:
    observations = [observation(1, "0.01"), observation(-1, "0.02"), observation(0, "0.03")]

    report = ExpectancyStats.of(observations)

    assert report.sample_size == 3
    assert report.triggered == 1  # only feature_value > 0


def test_conditional_expectancy_and_median_are_over_triggered_observations_only() -> None:
    observations = [
        observation(1, "0.02"), observation(1, "0.04"), observation(1, "0.06"),
        observation(-1, "-1.00"),  # not triggered: must not pollute the mean or median
    ]  # fmt: skip

    report = ExpectancyStats.of(observations)

    assert report.conditional_expectancy == Decimal("0.04")
    assert report.median_return == Decimal("0.04")


def test_hit_rate_is_the_fraction_of_triggered_observations_with_a_positive_return() -> None:
    observations = [
        observation(1, "0.01"), observation(1, "-0.01"),
        observation(1, "0.02"), observation(1, "0.00"),
    ]  # fmt: skip

    report = ExpectancyStats.of(observations)

    assert report.hit_rate == Decimal("0.5")


def test_cost_adjusted_expectancy_uses_the_cost_adjusted_field() -> None:
    observations = [
        observation(1, "0.02", "0.01"),
        observation(1, "0.04", "0.03"),
    ]

    report = ExpectancyStats.of(observations)

    assert report.conditional_expectancy == Decimal("0.03")
    assert report.cost_adjusted_expectancy == Decimal("0.02")


def test_a_perfectly_monotonic_relationship_has_rank_ic_of_one() -> None:
    observations = [observation(i, str(Decimal(i) / 100)) for i in range(1, 6)]

    report = ExpectancyStats.of(observations)

    assert report.rank_ic == Decimal(1)


def test_an_inverse_relationship_has_rank_ic_of_negative_one() -> None:
    observations = [observation(i, str(Decimal(6 - i) / 100)) for i in range(1, 6)]

    report = ExpectancyStats.of(observations)

    assert report.rank_ic == Decimal(-1)


def test_rank_ic_is_none_when_the_feature_never_varies() -> None:
    observations = [observation(1, "0.01"), observation(1, "0.02"), observation(1, "0.03")]

    report = ExpectancyStats.of(observations)

    assert report.rank_ic is None


def test_decile_spread_needs_at_least_ten_observations() -> None:
    observations = [observation(i, "0.01") for i in range(9)]

    report = ExpectancyStats.of(observations)

    assert report.decile_spread is None


def test_decile_spread_is_top_minus_bottom_by_feature_value() -> None:
    observations = [observation(i, str(Decimal(i) / 100)) for i in range(10)]

    report = ExpectancyStats.of(observations)

    # one observation per decile: top feature_value=9 (return 0.09), bottom feature_value=0 (0.00)
    assert report.decile_spread == Decimal("0.09")


def test_t_statistic_is_none_for_a_single_triggered_observation() -> None:
    observations = [observation(1, "0.01"), observation(-1, "-0.01")]

    report = ExpectancyStats.of(observations)

    assert report.t_statistic is None


def test_t_statistic_is_none_when_triggered_returns_have_no_spread() -> None:
    observations = [observation(1, "0.01"), observation(1, "0.01"), observation(1, "0.01")]

    report = ExpectancyStats.of(observations)

    assert report.t_statistic is None


def test_t_statistic_is_positive_when_the_triggered_mean_is_positive() -> None:
    observations = [observation(1, "0.01"), observation(1, "0.02"), observation(1, "0.03")]

    report = ExpectancyStats.of(observations)

    assert report.t_statistic is not None
    assert report.t_statistic > Decimal(0)
