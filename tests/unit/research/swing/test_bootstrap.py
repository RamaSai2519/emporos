"""EM-223: the §3.4 block bootstrap: deterministic, block-structured, and honest about ruin."""

from __future__ import annotations

import pytest

from emporos.research.swing.bootstrap import BlockBootstrap


def test_the_same_returns_and_seed_give_the_same_report() -> None:
    returns = [0.01, -0.02, 0.005, 0.0, -0.01] * 20

    a = BlockBootstrap(paths=200, seed=7).report(returns)
    b = BlockBootstrap(paths=200, seed=7).report(returns)

    assert a == b


def test_a_different_seed_can_give_a_different_report() -> None:
    returns = [0.03, -0.05, 0.02, -0.01, 0.04] * 20

    a = BlockBootstrap(paths=300, seed=1).report(returns)
    b = BlockBootstrap(paths=300, seed=2).report(returns)

    assert a != b


def test_a_series_that_only_rises_never_ruins_or_loses() -> None:
    report = BlockBootstrap(paths=200).report([0.001] * 100)

    assert (report.p_drawdown_30, report.p_year_negative) == (0.0, 0.0)
    assert report.median_year_return == pytest.approx(1.001**252 - 1)
    assert report.passes_aggressive_bar


def test_a_series_that_only_falls_always_ruins_and_always_loses() -> None:
    report = BlockBootstrap(paths=200).report([-0.005] * 100)

    assert (report.p_drawdown_30, report.p_year_negative) == (1.0, 1.0)  # 0.995^252 is -72%
    assert not report.passes_aggressive_bar


def test_a_single_25_percent_drop_is_not_a_30_percent_drawdown_but_two_are() -> None:
    one_drop = [0.0] * 60 + [-0.25] + [0.0] * 60
    report = BlockBootstrap(paths=200, block=20).report(one_drop)

    # 12 blocks of 20 sessions drawn from 122 start points: paths that draw the drop twice fall
    # 1 - 0.75^2 = 43.75%; those that draw it once fall 25%; so ruin is possible but not certain
    assert 0.0 < report.p_drawdown_30 < 1.0


def test_blocks_keep_runs_together() -> None:
    # alternating +10% / -10%: a path built from whole blocks (block=2) always nets out every pair,
    # a per-day shuffle would not. So with blocks the year return is (0.99)^126 - 1 exactly.
    alternating = [0.10, -0.10] * 50
    report = BlockBootstrap(paths=100, block=2, horizon=252).report(alternating)

    assert report.median_year_return == pytest.approx(0.99**126 - 1)


def test_a_series_shorter_than_a_block_cannot_be_bootstrapped() -> None:
    with pytest.raises(ValueError, match="at least 20"):
        BlockBootstrap().report([0.01] * 19)


@pytest.mark.parametrize("kwargs", [{"paths": 0}, {"block": 0}, {"horizon": 0}])
def test_the_parameters_are_positive(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="positive"):
        BlockBootstrap(**kwargs)


def test_the_defaults_are_the_plans() -> None:
    b = BlockBootstrap()
    report = b.report([0.001] * 40)

    assert (report.paths, report.block, report.horizon) == (10_000, 20, 252)
