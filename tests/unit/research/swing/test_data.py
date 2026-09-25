"""EM-223: the swing series and dataset, the as-of view, and the quarantine flattening."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.unit.research.swing.support import QuarantineSet, dataset, series, sessions

from emporos.research.adjustments import ActionKind, AdjustmentFactor, AdjustmentLedger
from emporos.research.swing.data import AsOfView, SwingDataset, SwingSeries

X = "NSE:1"
DAYS = sessions(5)


class TestSeries:
    def test_without_actions_analysis_is_raw_and_multipliers_are_one(self) -> None:
        s = series(X, DAYS, [("100", "101")] * 5)

        assert s.analysis == s.raw
        assert s.multipliers == (Decimal(1),) * 5
        assert s.neutralised == ()

    def test_a_split_scales_earlier_analysis_bars_and_multipliers(self) -> None:
        ledger = AdjustmentLedger(
            [AdjustmentFactor(X, DAYS[3], Decimal("0.5"), ActionKind.SPLIT, "s")]
        )
        s = series(X, DAYS, [("100", "100")] * 3 + [("50", "60"), ("60", "66")], ledger)

        assert [b.close.amount for b in s.analysis] == [50, 50, 50, 60, 66]
        assert s.multipliers[:3] == (Decimal("0.5"),) * 3
        assert s.multipliers[3:] == (Decimal(1), Decimal(1))
        assert s.raw[0].close.amount == 100  # fills still use what was traded

    def test_a_quarantined_gap_is_flattened_so_no_return_contains_it(self) -> None:
        raw = [("100", "100"), ("100", "100"), ("140", "141"), ("141", "142"), ("142", "143")]
        s = series(X, DAYS, raw, quarantine=QuarantineSet({X: [DAYS[2]]}))

        closes = [b.close.amount for b in s.analysis]
        assert closes[:2] == [140, 140]  # the earlier bars now sit at the post-gap level
        assert s.analysis[2].open.amount == closes[1]  # no gap into the quarantined session
        assert closes[2:] == [141, 142, 143]
        assert s.neutralised == (DAYS[2],)
        assert s.multipliers[0] == Decimal("1.4")

    def test_two_quarantined_gaps_compound(self) -> None:
        raw = [("100", "100"), ("200", "200"), ("200", "200"), ("400", "400"), ("400", "400")]
        s = series(X, DAYS, raw, quarantine=QuarantineSet({X: [DAYS[1], DAYS[3]]}))

        assert [b.close.amount for b in s.analysis] == [400, 400, 400, 400, 400]
        assert s.neutralised == (DAYS[1], DAYS[3])

    def test_the_parts_must_cover_the_same_sessions(self) -> None:
        s = series(X, DAYS, [("1", "1")] * 5)

        with pytest.raises(ValueError, match="same sessions"):
            SwingSeries(X, s.days[:4], s.raw, s.analysis, s.multipliers, ())

    def test_index_lookups(self) -> None:
        s = series(X, [DAYS[0], DAYS[2]], [("1", "1"), ("2", "2")])

        assert s.index_of(DAYS[2]) == 1
        assert s.index_of(DAYS[1]) is None
        assert s.last_index_on_or_before(DAYS[1]) == 0
        assert s.last_index_on_or_before(date(2000, 1, 1)) is None


class TestDataset:
    def test_the_calendar_is_the_union_of_sessions(self) -> None:
        d = dataset(
            series(X, DAYS[:3], [("1", "1")] * 3), series("NSE:2", DAYS[2:], [("1", "1")] * 3)
        )

        assert d.calendar == tuple(DAYS)
        assert d.instrument_ids == (X, "NSE:2")
        assert d.bar_on(X, DAYS[4]) is None
        assert d.bar_on("NSE:2", DAYS[4]) == 2

    def test_a_name_twice_is_refused(self) -> None:
        one = series(X, DAYS, [("1", "1")] * 5)

        with pytest.raises(ValueError, match="twice"):
            SwingDataset([one, one])


class TestAsOfView:
    def test_history_ends_on_the_decision_session(self) -> None:
        d = dataset(series(X, DAYS, [(str(i), str(i)) for i in range(1, 6)]))

        view = AsOfView(d, DAYS[2])

        assert [b.close.amount for b in view.history(X, 10)] == [1, 2, 3]
        assert [b.close.amount for b in view.history(X, 2)] == [2, 3]
        assert view.sessions() == tuple(DAYS[:3])

    def test_a_name_that_did_not_trade_on_the_day_shows_its_last_bars(self) -> None:
        d = dataset(
            series(X, DAYS[:2], [("1", "1"), ("2", "2")]), series("NSE:2", DAYS, [("5", "5")] * 5)
        )

        assert [b.close.amount for b in AsOfView(d, DAYS[4]).history(X, 5)] == [1, 2]

    def test_a_name_that_had_not_started_has_no_history(self) -> None:
        d = dataset(series(X, DAYS[3:], [("1", "1")] * 2), series("NSE:2", DAYS, [("5", "5")] * 5))

        assert AsOfView(d, DAYS[1]).history(X, 5) == ()
        assert not AsOfView(d, DAYS[1]).neutralised_within(X, 5)

    def test_neutralised_sessions_are_visible_only_once_they_have_happened(self) -> None:
        raw = [("100", "100"), ("100", "100"), ("140", "141"), ("141", "142"), ("142", "143")]
        d = dataset(series(X, DAYS, raw, quarantine=QuarantineSet({X: [DAYS[2]]})))

        assert not AsOfView(d, DAYS[1]).neutralised_within(X, 5)
        assert AsOfView(d, DAYS[2]).neutralised_within(X, 5)
        assert not AsOfView(d, DAYS[4]).neutralised_within(X, 2)  # the window is the last 2 bars
