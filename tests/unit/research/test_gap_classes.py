"""EM-221: real move or data artifact, the rule in order, with the reason on every verdict."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from emporos.research.discontinuities import DiscontinuityStatus, Finding
from emporos.research.gap_classes import GapClass, GapClassifier

DAY = date(2020, 3, 23)


class Index:
    def __init__(self, move: str | None) -> None:
        self._move = None if move is None else Decimal(move)

    def close_to_close(self, day: date) -> Decimal | None:
        return self._move


def finding(ratio: str, status: DiscontinuityStatus = DiscontinuityStatus.UNEXPLAINED,
            shape: str | None = None) -> Finding:  # fmt: skip
    from fractions import Fraction

    return Finding(
        "NSE:1", DAY, date(2020, 3, 20), Decimal(ratio), Decimal(1), status,
        Fraction(shape) if shape else None,
    )  # fmt: skip


def classify(ratio: str, index_move: str | None, **kw: object) -> GapClass:
    return GapClassifier(Index(index_move)).classify(finding(ratio, **kw)).gap_class  # type: ignore[arg-type]


class TestRule:
    def test_a_gap_the_ledger_explains_is_explained(self) -> None:
        assert classify("0.5", "0", status=DiscontinuityStatus.EXPLAINED) is GapClass.EXPLAINED

    def test_a_gap_the_market_moved_with_is_real(self) -> None:
        # 2020-03-23: a name gapped -19.9%, NIFTY fell 13.0% close to close
        assert classify("0.801", "-0.130") is GapClass.REAL

    def test_the_index_must_move_at_least_half_as_much_and_the_same_way(self) -> None:
        assert classify("0.80", "-0.10") is GapClass.REAL  # exactly half
        # under half, or the other way: the market did not move with it, so the size rules decide,
        # and a 20% gap that is not split-shaped is an ordinary real move all the same
        assert classify("0.80", "-0.09") is GapClass.REAL
        assert classify("0.80", "+0.10") is GapClass.REAL
        assert classify("0.50", "-0.09", shape="1/2") is GapClass.ARTIFACT  # half-gap, split-shaped
        assert (
            classify("0.50", "+0.30", shape="1/2") is GapClass.ARTIFACT
        )  # index went the other way

    def test_a_market_move_beats_the_split_shape(self) -> None:
        assert classify("0.5", "-0.30", shape="1/2") is GapClass.REAL

    def test_a_split_shaped_gap_without_the_market_is_an_artifact(self) -> None:
        assert classify("0.5003", "0.001", shape="1/2") is GapClass.ARTIFACT

    def test_a_gap_over_25_percent_without_the_market_is_an_artifact(self) -> None:
        assert classify("0.541", "0.0065") is GapClass.ARTIFACT
        assert classify("1.26", "0.0") is GapClass.ARTIFACT

    def test_exactly_25_percent_is_not_over_it(self) -> None:
        assert classify("0.75", "0.0") is GapClass.REAL
        assert classify("1.25", "0.0") is GapClass.REAL

    def test_an_ordinary_large_single_name_move_is_real(self) -> None:
        assert classify("0.802", "-0.005") is GapClass.REAL  # a lower-circuit print, no market move

    def test_no_index_data_means_the_size_rules_decide(self) -> None:
        assert classify("0.8", None) is GapClass.REAL
        assert classify("0.5", None, shape="1/2") is GapClass.ARTIFACT

    def test_every_verdict_carries_its_reason_and_the_index_move(self) -> None:
        verdict = GapClassifier(Index("-0.13")).classify(finding("0.801"))

        assert "the index moved -13.0% the same session" in verdict.reason
        assert verdict.index_move == Decimal("-0.13")
