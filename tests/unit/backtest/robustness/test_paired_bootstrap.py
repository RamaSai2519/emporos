from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.backtest.robustness.paired_bootstrap import PairedBootstrapReport, PairedDayBootstrap

D = Decimal


def _series(n: int, shift: str = "0") -> list[Decimal]:
    """A deterministic, varying daily-return series, optionally shifted by a constant."""
    pattern = [D("0.010"), D("-0.004"), D("0.007"), D("-0.002"), D("0.003")]
    return [pattern[i % len(pattern)] + D(shift) for i in range(n)]


def _report(result: PairedBootstrapReport | str) -> PairedBootstrapReport:
    assert isinstance(result, PairedBootstrapReport), result
    return result


def test_a_constant_improvement_is_distinguishable_from_noise() -> None:
    report = _report(
        PairedDayBootstrap(seed=1, resamples=300).compare(_series(60), _series(60, "0.001"))
    )

    interval = report.mean_daily_return
    assert interval.observed == D("0.001")
    assert interval.low > 0  # the pairing removes the day-to-day noise entirely


def test_identical_arms_give_a_zero_interval() -> None:
    report = _report(PairedDayBootstrap(seed=1, resamples=100).compare(_series(40), _series(40)))

    assert report.mean_daily_return.low == report.mean_daily_return.high == 0


def test_a_worse_treatment_has_an_upper_bound_below_zero() -> None:
    report = _report(
        PairedDayBootstrap(seed=1, resamples=200).compare(_series(60), _series(60, "-0.002"))
    )

    assert report.mean_daily_return.high < 0


def test_the_same_seed_gives_the_same_interval() -> None:
    a = PairedDayBootstrap(seed=7, resamples=100).compare(_series(30), _series(30, "0.0005"))
    b = PairedDayBootstrap(seed=7, resamples=100).compare(_series(30), _series(30, "0.0005"))
    c = PairedDayBootstrap(seed=8, resamples=100).compare(_series(30), _series(30, "0.0005"))

    assert a == b
    assert a != c


def test_a_sharpe_interval_is_reported_when_both_arms_vary() -> None:
    report = _report(
        PairedDayBootstrap(seed=1, resamples=100).compare(_series(40), _series(40, "0.002"))
    )

    assert report.daily_sharpe is not None
    assert report.daily_sharpe.observed is not None and report.daily_sharpe.observed > 0
    assert report.undefined_sharpe_resamples == 0


def test_an_arm_with_no_variance_leaves_the_sharpe_undefined_not_zero() -> None:
    flat = [D("0.001")] * 30

    report = _report(PairedDayBootstrap(seed=1, resamples=50).compare(flat, _series(30)))

    assert report.daily_sharpe is None
    assert report.undefined_sharpe_resamples == 50


def test_arms_that_do_not_share_their_days_cannot_be_paired() -> None:
    result = PairedDayBootstrap(seed=1).compare(_series(30), _series(29))

    assert isinstance(result, str) and "do not share" in result


def test_too_few_days_is_a_reason_not_a_tight_interval() -> None:
    result = PairedDayBootstrap(seed=1).compare(_series(5), _series(5, "0.01"))

    assert isinstance(result, str) and "fewer than the 20" in result


@pytest.mark.parametrize(
    "kwargs",
    [{"resamples": 0}, {"confidence": D("1")}, {"confidence": D("0")}, {"min_days": 1}],
)
def test_invalid_settings_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PairedDayBootstrap(seed=1, **kwargs)  # type: ignore[arg-type]
