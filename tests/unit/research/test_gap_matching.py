"""EM-221: a gap in the broker's series is explained by a later exchange action, or stays open."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from emporos.research.adjustments import ActionKind
from emporos.research.corporate_actions import ReadAction
from emporos.research.gap_matching import GapMatcher, RawGap

X = "NSE:1"
SOURCE = "NSE feed"


def gap(day: date, ratio: str, instrument: str = X) -> RawGap:
    return RawGap(instrument, day, date(day.year, day.month, 1), Decimal(ratio))


def action(
    ex: date, ratio: str, kind: ActionKind = ActionKind.SPLIT, subject: str = "s"
) -> ReadAction:
    return ReadAction(ex, kind, Decimal(ratio), subject)


class TestMatch:
    def test_a_gap_that_fits_a_later_split_is_dated_at_the_gap_not_the_ex_date(self) -> None:
        gaps = [gap(date(2022, 5, 10), "0.1998")]
        actions = {X: [action(date(2024, 5, 15), "0.2", subject="Split 10 to 2")]}

        result = GapMatcher().match(gaps, actions, SOURCE)

        (factor,) = result.factors
        assert (factor.ex_date, factor.ratio, factor.kind) == (
            date(2022, 5, 10),
            Decimal("0.2"),
            ActionKind.SPLIT,
        )
        assert "Split 10 to 2 (ex 2024-05-15)" in factor.note
        assert "changes basis on 2022-05-10" in factor.source
        assert result.unmatched == ()

    def test_the_exchanges_exact_ratio_is_used_not_the_noisy_gap(self) -> None:
        result = GapMatcher().match(
            [gap(date(2022, 5, 10), "0.7548")], {X: [action(date(2022, 8, 17), "0.75")]}, SOURCE
        )

        assert result.factors[0].ratio == Decimal("0.75")

    def test_an_action_on_the_gap_day_itself_matches(self) -> None:
        result = GapMatcher().match(
            [gap(date(2022, 7, 28), "0.1023")], {X: [action(date(2022, 7, 28), "0.1")]}, SOURCE
        )

        assert len(result.factors) == 1

    def test_an_action_before_the_previous_session_does_not_match(self) -> None:
        result = GapMatcher().match(
            [gap(date(2022, 5, 10), "0.5")], {X: [action(date(2022, 4, 20), "0.5")]}, SOURCE
        )

        assert result.factors == ()
        assert len(result.unmatched) == 1

    def test_a_gap_beyond_the_tolerance_stays_open(self) -> None:
        result = GapMatcher().match(
            [gap(date(2022, 5, 10), "0.7332")], {X: [action(date(2022, 6, 1), "0.8")]}, SOURCE
        )

        assert result.factors == ()

    def test_a_gap_that_fits_two_actions_together_takes_their_product(self) -> None:
        actions = {X: [action(date(2022, 6, 1), "0.5"), action(date(2022, 7, 1), "0.8")]}

        result = GapMatcher().match([gap(date(2022, 5, 10), "0.4")], actions, SOURCE)

        (factor,) = result.factors
        assert factor.ratio == Decimal("0.40")
        assert factor.kind is ActionKind.SPLIT

    def test_mixed_kinds_make_kind_other(self) -> None:
        actions = {X: [action(date(2022, 6, 1), "0.5", ActionKind.BONUS),
                       action(date(2022, 7, 1), "0.8", ActionKind.SPLIT)]}  # fmt: skip

        result = GapMatcher().match([gap(date(2022, 5, 10), "0.4")], actions, SOURCE)

        assert result.factors[0].kind is ActionKind.OTHER

    def test_an_action_explains_one_gap_the_closest_one(self) -> None:
        gaps = [gap(date(2018, 10, 1), "0.7332"), gap(date(2021, 3, 8), "0.7553")]
        actions = {X: [action(date(2021, 3, 18), "0.75")]}

        result = GapMatcher().match(gaps, actions, SOURCE)

        (factor,) = result.factors
        assert factor.ex_date == date(2021, 3, 8)  # 0.7% off beats 2.2% off
        assert [g.day for g in result.unmatched] == [date(2018, 10, 1)]

    def test_another_instruments_actions_do_not_explain_a_gap(self) -> None:
        result = GapMatcher().match(
            [gap(date(2022, 5, 10), "0.5")], {"NSE:2": [action(date(2022, 6, 1), "0.5")]}, SOURCE
        )

        assert result.factors == ()

    def test_factors_come_out_ordered(self) -> None:
        gaps = [gap(date(2022, 5, 10), "0.5", "NSE:2"), gap(date(2020, 5, 10), "0.5")]
        actions = {"NSE:2": [action(date(2022, 6, 1), "0.5")], X: [action(date(2020, 6, 1), "0.5")]}

        result = GapMatcher().match(gaps, actions, SOURCE)

        assert [(f.instrument_id, f.ex_date.year) for f in result.factors] == [
            (X, 2020),
            ("NSE:2", 2022),
        ]

    @pytest.mark.parametrize("tolerance", ["0", "1", "-0.1"])
    def test_the_tolerance_is_a_fraction(self, tolerance: str) -> None:
        with pytest.raises(ValueError, match="fraction"):
            GapMatcher(Decimal(tolerance))
