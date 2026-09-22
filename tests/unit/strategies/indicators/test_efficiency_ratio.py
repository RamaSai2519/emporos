"""Kaufman's Efficiency Ratio, hand-checked."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.strategies.indicators.efficiency_ratio import EfficiencyRatio


def _feed(er: EfficiencyRatio, closes: list[str]) -> Decimal | None:
    value = None
    for close in closes:
        value = er.update(Decimal(close))
    return value


class TestEfficiencyRatio:
    def test_not_ready_before_period_plus_one_closes(self) -> None:
        er = EfficiencyRatio(4)
        assert _feed(er, ["100", "101", "102", "103"]) is None
        assert not er.ready

    def test_a_straight_line_move_is_perfectly_efficient(self) -> None:
        er = EfficiencyRatio(4)
        value = _feed(er, ["100", "101", "102", "103", "104"])
        assert value == Decimal("1")
        assert er.ready

    def test_a_round_trip_to_the_same_price_is_zero_efficiency(self) -> None:
        er = EfficiencyRatio(4)
        value = _feed(er, ["100", "105", "100", "105", "100"])
        assert value == Decimal("0")

    def test_partial_efficiency_matches_hand_computed_ratio(self) -> None:
        er = EfficiencyRatio(4)
        # deltas: +5, +3, -2, +2; net = |108-100| = 8; total = 5+3+2+2 = 12; ER = 8/12 = 2/3
        value = _feed(er, ["100", "105", "108", "106", "108"])
        assert value == Decimal("8") / Decimal("12")

    def test_the_window_slides_and_drops_the_oldest_close(self) -> None:
        er = EfficiencyRatio(2)
        _feed(er, ["100", "101", "102"])  # net=2, total=2 -> ER=1
        assert er.value == Decimal("1")
        value = er.update(Decimal("100"))  # window now (101, 102, 100): net=1, total=1+2=3
        assert value == Decimal("1") / Decimal("3")

    def test_rejects_a_non_positive_period(self) -> None:
        with pytest.raises(ValueError):
            EfficiencyRatio(0)
