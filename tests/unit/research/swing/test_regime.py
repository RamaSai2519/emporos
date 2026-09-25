"""EM-228/229: the trend regime, the rebalance calendars, and the stop."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.unit.research.swing.support import (
    MONDAY,
    bar,
    context,
    dataset,
    holding,
    series,
    sessions,
)

from emporos.research.swing.data import AsOfView
from emporos.research.swing.regime import (
    IndexSeries,
    IndexTrendRegime,
    MonthlyCalendar,
    QuarterlyCalendar,
    WeeklyCalendar,
    YearlyCalendar,
)
from emporos.research.swing.rules import LossStop

DAYS = sessions(10)
X = "NSE:1"


def index(closes: list[str]) -> IndexSeries:
    return IndexSeries([bar("NSE:99926000", d, c, c) for d, c in zip(DAYS, closes, strict=False)])


def on(regime: IndexTrendRegime, day_index: int) -> bool:
    data = dataset(series(X, DAYS, [("1", "1")] * 10))
    return regime.is_on(context(data, DAYS[day_index]))


class TestIndexSeries:
    def test_closes_up_to_a_day_are_the_last_n_on_or_before_it(self) -> None:
        s = index(["1", "2", "3", "4"])

        assert s.closes_up_to(DAYS[2], 2) == [Decimal(2), Decimal(3)]
        assert s.closes_up_to(DAYS[2], 10) == [Decimal(1), Decimal(2), Decimal(3)]
        assert s.closes_up_to(date(2000, 1, 1), 3) == []
        assert (len(s), s.first_day) == (4, DAYS[0])

    def test_it_must_be_oldest_first(self) -> None:
        with pytest.raises(ValueError, match="oldest first"):
            IndexSeries([bar("i", DAYS[1], "1", "1"), bar("i", DAYS[0], "1", "1")])


class TestRegime:
    CLOSES = ("10", "10", "10", "12", "9", "9", "9", "20", "20", "20")

    def test_on_when_the_close_is_above_the_average_of_the_window_including_itself(self) -> None:
        regime = IndexTrendRegime(index(list(self.CLOSES)), window=3)

        assert on(regime, 3)  # 12 > (10 + 10 + 12) / 3
        assert not on(regime, 4)  # 9 < (10 + 12 + 9) / 3
        assert not on(regime, 6)  # 9 == 9: not above

    def test_it_has_no_opinion_before_the_window_is_full_so_it_is_off(self) -> None:
        regime = IndexTrendRegime(index(list(self.CLOSES)), window=3)

        assert not on(regime, 0)
        assert not on(regime, 1)
        assert regime.first_defined_day(DAYS) == DAYS[2]

    def test_a_day_the_index_did_not_trade_uses_its_last_close(self) -> None:
        sparse = IndexSeries([bar("i", DAYS[0], "1", "1"), bar("i", DAYS[1], "1", "1"),
                              bar("i", DAYS[3], "5", "5")])  # fmt: skip
        regime = IndexTrendRegime(sparse, window=3)

        assert on(regime, 5)  # DAYS[5]: last three closes 1, 1, 5

    def test_it_never_reads_a_close_after_the_decision_day(self) -> None:
        early = IndexTrendRegime(index(["10", "10", "10", "1000"]), window=3)

        assert not on(early, 2)  # the 1000 on DAYS[3] is in the future

    def test_a_window_needs_at_least_two_closes(self) -> None:
        with pytest.raises(ValueError, match="at least two"):
            IndexTrendRegime(index(["1"]), window=1)

    def test_an_empty_series_is_never_on(self) -> None:
        regime = IndexTrendRegime(IndexSeries([]), window=3)

        assert not on(regime, 5)
        assert regime.first_defined_day(DAYS) is None


class TestCalendars:
    # a holiday-shortened week: Mon 5, Tue 6, Wed 7 traded; Thu 8, Fri 9 closed; Mon 12, Tue 13...
    TRADED = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 12),
              date(2026, 1, 13), date(2026, 1, 30), date(2026, 2, 2), date(2026, 2, 3))  # fmt: skip

    def flags(self, calendar: WeeklyCalendar | MonthlyCalendar) -> list[bool]:
        data = dataset(series(X, self.TRADED, [("1", "1")] * len(self.TRADED)))
        return [calendar.is_rebalance(AsOfView(data, d)) for d in self.TRADED]

    def test_weekly_is_the_first_session_of_each_calendar_week(self) -> None:
        assert self.flags(WeeklyCalendar()) == [True, False, False, True, False, True, True, False]

    def test_monthly_is_the_first_session_of_each_calendar_month(self) -> None:
        assert self.flags(MonthlyCalendar()) == [
            True,
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        ]

    def test_the_first_session_in_the_data_is_a_rebalance(self) -> None:
        data = dataset(series(X, DAYS, [("1", "1")] * 10))

        assert AsOfView(data, MONDAY).previous_session() is None
        assert WeeklyCalendar().is_rebalance(AsOfView(data, MONDAY))

    def test_a_week_that_spans_a_year_end_is_one_week(self) -> None:
        days = [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 2)]
        data = dataset(series(X, days, [("1", "1")] * 4))

        flags = [WeeklyCalendar().is_rebalance(AsOfView(data, d)) for d in days]

        assert flags == [True, False, False, False]  # ISO week 1 of 2026 starts on Dec 29


class TestLossStop:
    def test_the_fall_that_stops_makes_the_loss_two_percent_of_the_book(self) -> None:
        stop = LossStop()
        one_fifth = holding(X, price="100", value="20000", equity="100000")
        one_tenth = holding(X, price="100", value="10000", equity="100000")

        assert stop.fall_that_stops(one_fifth) == Decimal("0.10")  # 2% of 100k is 2k, 10% of 20k
        assert stop.fall_that_stops(one_tenth) == Decimal("0.20")

    def test_it_is_hit_at_the_stop_price_and_not_above_it(self) -> None:
        stop = LossStop()
        h = holding(X, price="100", value="20000", equity="100000")

        assert stop.is_hit(h, Decimal("90"))
        assert stop.is_hit(h, Decimal("50"))  # a gap through the stop
        assert not stop.is_hit(h, Decimal("90.01"))

    def test_the_risk_is_a_fraction_of_the_book(self) -> None:
        for bad in ("0", "1", "-0.1"):
            with pytest.raises(ValueError, match="fraction"):
                LossStop(Decimal(bad))


class TestLongerCalendars:
    TRADED = (date(2025, 12, 30), date(2026, 1, 2), date(2026, 2, 3), date(2026, 3, 31),
              date(2026, 4, 1), date(2026, 6, 30), date(2026, 7, 1), date(2027, 1, 4))  # fmt: skip

    def flags(self, calendar: QuarterlyCalendar | YearlyCalendar) -> list[bool]:
        data = dataset(series(X, self.TRADED, [("1", "1")] * len(self.TRADED)))
        return [calendar.is_rebalance(AsOfView(data, d)) for d in self.TRADED]

    def test_quarterly_is_the_first_session_of_each_calendar_quarter(self) -> None:
        assert self.flags(QuarterlyCalendar()) == [
            True,
            True,
            False,
            False,
            True,
            False,
            True,
            True,
        ]

    def test_yearly_is_the_first_session_of_each_calendar_year(self) -> None:
        assert self.flags(YearlyCalendar()) == [True, True, False, False, False, False, False, True]
