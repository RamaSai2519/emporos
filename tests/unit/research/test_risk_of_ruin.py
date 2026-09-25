"""EM-226: the block-bootstrap risk of ruin (PROFIT_PLAN §3.4), pinned on series whose answer is
known without simulation."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from emporos.research.risk_of_ruin import BlockBootstrapRuin

D = Decimal


def ruin(capital: str = "1000", **kw: int) -> BlockBootstrapRuin:
    return BlockBootstrapRuin(
        D(capital), random.Random(7), paths=200, horizon_days=40, block_days=5, **kw
    )


def test_a_history_that_only_gains_can_neither_fall_nor_end_negative() -> None:
    report = ruin().assess([D(5)] * 30)

    assert (report.p_drawdown, report.p_negative) == (D(0), D(0))


def test_a_history_that_only_loses_always_falls_and_always_ends_negative() -> None:
    report = ruin().assess([D(-20)] * 30)  # 40 days x 20 = 800 lost, a 30% fall by day 15

    assert (report.p_drawdown, report.p_negative) == (D(1), D(1))


def test_blocks_keep_an_alternating_streak_whole() -> None:
    """Blocks of 4 taken from +10 x2 then -10 x2 cancel exactly, so no path ends below the start."""
    history = [D(10), D(10), D(-10), D(-10)] * 6
    report = BlockBootstrapRuin(
        D(1000), random.Random(1), paths=300, horizon_days=40, block_days=4
    ).assess(history)

    assert report.p_negative == D(0)  # ends where it began or above, never below


def test_the_same_seed_gives_the_same_report() -> None:
    history = [D(30), D(-25), D(10), D(-40), D(15), D(5)] * 8

    assert ruin().assess(history) == ruin().assess(history)


def test_a_higher_threshold_can_only_lower_the_chance_of_ruin() -> None:
    history = [D(60), D(-70), D(20), D(-50), D(30), D(-10)] * 8
    shallow = BlockBootstrapRuin(D(1000), random.Random(3), D("0.05"), 400, 60, 5).assess(history)
    deep = BlockBootstrapRuin(D(1000), random.Random(3), D("0.30"), 400, 60, 5).assess(history)

    assert deep.p_drawdown <= shallow.p_drawdown


def test_too_short_a_history_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 5 days"):
        ruin().assess([D(1)] * 4)


@pytest.mark.parametrize(
    "args",
    [(D(0),), (D(1000), random.Random(), D(0)), (D(1000), random.Random(), D("0.3"), 0)],
)
def test_nonsense_settings_are_refused(args: tuple[object, ...]) -> None:
    with pytest.raises((ValueError, TypeError)):
        BlockBootstrapRuin(*args)  # type: ignore[arg-type]
