"""EM-182: `CSCV` — Probability of Backtest Overfitting via Combinatorially Symmetric
Cross-Validation, over walk-forward per-window training scores."""

from __future__ import annotations

from decimal import Decimal
from math import comb

from emporos.backtest.robustness.pbo import CSCV, MAX_COMBINATIONS

D = Decimal


def test_needs_at_least_two_candidates() -> None:
    report = CSCV().evaluate({"A": [D(1), D(1), D(1), D(1)]})

    assert not report.computed
    assert report.reason is not None and "candidate" in report.reason


def test_needs_matching_window_counts_across_candidates() -> None:
    report = CSCV().evaluate({"A": [D(1), D(1), D(1), D(1)], "B": [D(1), D(1), D(1)]})

    assert not report.computed
    assert report.reason == "candidates were not scored over the same windows"


def test_needs_at_least_four_windows() -> None:
    report = CSCV().evaluate({"A": [D(1), D(1)], "B": [D(0), D(0)]})

    assert not report.computed
    assert report.reason is not None and "window" in report.reason


def test_an_odd_window_count_drops_the_last_window() -> None:
    report = CSCV().evaluate({
        "A": [D(1), D(1), D(1), D(1), D(1)],
        "B": [D(0), D(0), D(0), D(0), D(0)],
    })  # fmt: skip

    assert report.computed
    assert report.block_count == 4  # the 5th window was dropped to keep the split symmetric


def test_a_consistently_superior_candidate_has_zero_probability_of_overfitting() -> None:
    """A always outscores B in every window, so whichever half is "in-sample", A is always the
    in-sample winner AND the out-of-sample winner too — never below the out-of-sample median."""
    report = CSCV().evaluate({
        "A": [D(1), D(1), D(1), D(1)],
        "B": [D(0), D(0), D(0), D(0)],
    })  # fmt: skip

    assert report.computed
    assert report.combination_count == comb(4, 2)
    assert report.probability_of_overfitting == D(0)
    assert report.mean_logit is not None and report.mean_logit > D(0)


def test_a_coin_flip_pattern_shows_a_positive_probability_of_overfitting() -> None:
    """A wins the first half of the windows and loses the second; B is the mirror image — the
    in-sample winner is not consistently the out-of-sample winner, the CSCV overfitting
    signature."""
    report = CSCV().evaluate({
        "A": [D(10), D(10), D(0), D(0)],
        "B": [D(0), D(0), D(10), D(10)],
    })  # fmt: skip

    pbo = report.probability_of_overfitting
    assert report.computed and pbo is not None
    assert D(0) < pbo < D(1)


def test_too_many_combinations_is_refused() -> None:
    block_count = 22  # C(22, 11) = 705,432 > MAX_COMBINATIONS
    assert comb(block_count, block_count // 2) > MAX_COMBINATIONS
    report = CSCV().evaluate({
        "A": [D(1)] * block_count,
        "B": [D(0)] * block_count,
    })  # fmt: skip

    assert not report.computed
    assert report.reason is not None and "exceeds" in report.reason
