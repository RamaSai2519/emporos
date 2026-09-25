"""EM-240: entries and exits on the program's fill rules."""

from __future__ import annotations

import pytest

from emporos.eventtrader.replay.fills import (
    EntryFill,
    EntryFiller,
    ExitReason,
    ExitSimulator,
    NoEntry,
    entry_time,
)
from emporos.eventtrader.stages.models import Side
from emporos.research.scans.base import ScanExecution
from tests.unit.eventtrader.replay.fakes import (
    FRI,
    MON,
    NEXT_MON,
    SAT,
    THU,
    TUE,
    WED,
    D,
    FakeMarket,
    at,
    daily,
    flat_day,
    replace_bar,
)

X = "NSE:1"
EXEC = ScanExecution(position_value=D(50000))


def market(*days: object) -> FakeMarket:
    m = FakeMarket([MON, TUE, WED, THU, FRI, NEXT_MON])
    for day in days:
        m.put_day(X, day, flat_day(X, day))  # type: ignore[arg-type]
    return m


class TestEntryTime:
    def test_an_in_session_decision_is_placed_at_once(self) -> None:
        m = market()

        assert entry_time(at(MON, 10, 2), m) == at(MON, 10, 2)
        assert entry_time(at(MON, 15, 24), m) == at(MON, 15, 24)

    def test_after_1525_or_after_the_close_it_waits_for_the_next_session_at_0920(self) -> None:
        m = market()

        assert entry_time(at(MON, 15, 25), m) == at(TUE, 9, 20)
        assert entry_time(at(MON, 20, 2), m) == at(TUE, 9, 20)

    def test_before_the_open_it_is_the_same_days_0920_so_the_opening_print_is_avoided(self) -> None:
        assert entry_time(at(MON, 8, 50), market()) == at(MON, 9, 20)

    def test_a_weekend_or_holiday_decision_goes_to_the_next_session(self) -> None:
        assert entry_time(at(FRI, 21, 0), market()) == at(NEXT_MON, 9, 20)
        assert entry_time(at(SAT, 11, 0), market()) == at(NEXT_MON, 9, 20)

    def test_no_later_session_in_the_data_is_no_entry(self) -> None:
        assert entry_time(at(NEXT_MON, 20, 0), market()) is None


class TestEntry:
    def test_the_anchor_is_the_last_bar_closed_and_the_fill_bar_is_the_first_that_starts_after(
        self,
    ) -> None:
        m = market()
        day = flat_day(X, MON, 100)
        day = replace_bar(day, 10, 0, c=101, h=101)  # the bar closing at 10:05 ends at 101...
        day = replace_bar(
            day, 10, 5, o=101, h=101, low=100, c=100.5
        )  # ...and trades through 101.05
        m.put_day(X, MON, day)

        fill = EntryFiller(m, EXEC).fill(X, Side.LONG, at(MON, 10, 2), 100)

        # decision 10:02: the last bar closed is 09:55-10:00 (close 100); the first to start after
        # it is 10:05
        assert isinstance(fill, EntryFill) and fill.ts == at(MON, 10, 5).astimezone(fill.ts.tzinfo)
        assert fill.reference == D(100)

    def test_a_long_needs_the_bar_to_trade_strictly_below_the_limit(self) -> None:
        m = market(MON)
        # limit for a buy = 100 x 1.0005 = 100.05 (already on the tick); the flat bar's low is 100
        assert isinstance(EntryFiller(m, EXEC).fill(X, Side.LONG, at(MON, 10, 2), 10), EntryFill)
        day = replace_bar(flat_day(X, MON, 100), 10, 5, o=100.5, h=101, low=100.05, c=100.6)
        m.put_day(X, MON, day)

        assert EntryFiller(m, EXEC).fill(X, Side.LONG, at(MON, 10, 2), 10) is NoEntry.NOT_THROUGH

    def test_a_short_needs_the_bar_to_trade_strictly_above_its_limit(self) -> None:
        m = market(MON)
        day = replace_bar(flat_day(X, MON, 100), 10, 5, o=99.5, h=99.95, low=99, c=99.4)
        m.put_day(X, MON, day)

        assert EntryFiller(m, EXEC).fill(X, Side.SHORT, at(MON, 10, 2), 10) is NoEntry.NOT_THROUGH
        up = replace_bar(flat_day(X, MON, 100), 10, 5, h=100.2)
        m.put_day(X, MON, up)
        assert isinstance(EntryFiller(m, EXEC).fill(X, Side.SHORT, at(MON, 10, 2), 10), EntryFill)

    def test_the_order_must_fit_in_ten_percent_of_the_bars_volume(self) -> None:
        m = market()
        m.put_day(X, MON, replace_bar(flat_day(X, MON, 100), 10, 5, h=101, low=99, volume=1000))

        assert EntryFiller(m, EXEC).fill(X, Side.LONG, at(MON, 10, 2), 100) is not NoEntry.NO_VOLUME
        assert EntryFiller(m, EXEC).fill(X, Side.LONG, at(MON, 10, 2), 101) is NoEntry.NO_VOLUME

    def test_only_the_first_eligible_bar_is_tried_there_is_no_chasing(self) -> None:
        m = market()
        day = replace_bar(flat_day(X, MON, 100), 10, 5, o=100.5, h=101, low=100.5, c=101)
        day = replace_bar(
            day, 10, 10, o=101, h=101, low=99, c=100
        )  # this bar would fill, it is not tried
        m.put_day(X, MON, day)

        assert EntryFiller(m, EXEC).fill(X, Side.LONG, at(MON, 10, 2), 10) is NoEntry.NOT_THROUGH

    def test_after_hours_the_anchor_is_the_0915_bar_and_the_first_bar_is_0920(self) -> None:
        m = market()
        day = replace_bar(flat_day(X, TUE, 110), 9, 15, o=110, h=111, low=109, c=110.5)
        day = replace_bar(day, 9, 20, o=110.5, h=111, low=110, c=110.6)
        m.put_day(X, TUE, day)

        fill = EntryFiller(m, EXEC).fill(
            X, Side.LONG, entry_time(at(MON, 20, 0), m) or at(MON, 0), 10
        )

        assert isinstance(fill, EntryFill) and fill.day == TUE and fill.reference == D("110.5")
        assert fill.ts == at(TUE, 9, 20).astimezone(fill.ts.tzinfo) and fill.bar_index == 1

    def test_no_bars_means_no_entry(self) -> None:
        assert EntryFiller(market(), EXEC).fill(X, Side.LONG, at(MON, 10, 2), 1) is NoEntry.NO_BARS

    def test_a_decision_at_the_open_before_any_bar_has_closed_has_no_anchor(self) -> None:
        assert (
            EntryFiller(market(MON), EXEC).fill(X, Side.LONG, at(MON, 9, 15), 1)
            is NoEntry.NO_ANCHOR
        )


def entered(m: FakeMarket, hh: int = 10, mm: int = 2) -> EntryFill:
    fill = EntryFiller(m, EXEC).fill(X, Side.LONG, at(MON, hh, mm), 10)
    assert isinstance(fill, EntryFill)
    return fill


def in_market(day_bars: list) -> FakeMarket:  # type: ignore[type-arg]
    m = market()
    m.put_day(X, MON, day_bars)
    return m


class TestIntradayExit:
    def test_a_flat_day_is_squared_off_at_the_close_of_the_1510_bar(self) -> None:
        m = in_market(replace_bar(flat_day(X, MON, 100), 15, 10, c=100, h=100))
        exit_ = ExitSimulator(m, EXEC).intraday_exit(X, Side.LONG, entered(m), D(95), D(110))

        assert exit_.reason is ExitReason.SQUARE_OFF and exit_.ts == at(MON, 15, 15).astimezone(
            exit_.ts.tzinfo
        )

    def test_the_stop_exits_at_the_stop_and_the_target_at_the_target(self) -> None:
        stop = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, h=100, low=94, c=95))
        hit = ExitSimulator(stop, EXEC).intraday_exit(X, Side.LONG, entered(stop), D(95), D(110))
        assert (hit.reason, hit.price) == (ExitReason.STOP, D(95))

        win = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, h=112, low=100, c=111))
        hit = ExitSimulator(win, EXEC).intraday_exit(X, Side.LONG, entered(win), D(95), D(110))
        assert (hit.reason, hit.price) == (ExitReason.TARGET, D(110))

    def test_touching_a_level_exactly_is_a_hit(self) -> None:
        at_stop = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, h=100, low=95, c=97))
        assert (
            ExitSimulator(at_stop, EXEC)
            .intraday_exit(X, Side.LONG, entered(at_stop), D(95), D(110))
            .reason
            is ExitReason.STOP
        )
        at_target = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, h=110, low=100, c=105))
        assert (
            ExitSimulator(at_target, EXEC)
            .intraday_exit(X, Side.LONG, entered(at_target), D(95), D(110))
            .reason
            is ExitReason.TARGET
        )

    def test_a_bar_that_reaches_both_counts_as_the_stop(self) -> None:
        both = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, h=112, low=94, c=100))

        hit = ExitSimulator(both, EXEC).intraday_exit(X, Side.LONG, entered(both), D(95), D(110))

        assert hit.reason is ExitReason.STOP

    def test_a_bar_that_opens_beyond_the_stop_exits_at_its_open_not_the_stop(self) -> None:
        gap = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, o=90, h=91, low=89, c=90))

        hit = ExitSimulator(gap, EXEC).intraday_exit(X, Side.LONG, entered(gap), D(95), D(110))

        assert (hit.reason, hit.price) == (ExitReason.STOP, D(90))

    def test_a_short_stops_above_and_targets_below(self) -> None:
        m = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, h=106, low=100, c=105))
        fill = EntryFiller(m, EXEC).fill(X, Side.SHORT, at(MON, 10, 2), 10)
        assert isinstance(fill, EntryFill)

        stopped = ExitSimulator(m, EXEC).intraday_exit(X, Side.SHORT, fill, D(105), D(90))
        assert (stopped.reason, stopped.price) == (ExitReason.STOP, D(105))
        m2 = in_market(replace_bar(flat_day(X, MON, 100), 11, 0, h=100, low=89, c=91))
        fill2 = EntryFiller(m2, EXEC).fill(X, Side.SHORT, at(MON, 10, 2), 10)
        assert isinstance(fill2, EntryFill)
        won = ExitSimulator(m2, EXEC).intraday_exit(X, Side.SHORT, fill2, D(105), D(90))
        assert (won.reason, won.price) == (ExitReason.TARGET, D(90))

    def test_the_fill_bar_itself_and_bars_after_the_squareoff_are_not_exit_candidates(self) -> None:
        wild = replace_bar(flat_day(X, MON, 100), 10, 5, h=120, low=80, c=100)  # the fill bar
        wild = replace_bar(wild, 15, 15, h=120, low=80, c=100)  # after the 15:15 square-off
        m = in_market(wild)

        hit = ExitSimulator(m, EXEC).intraday_exit(X, Side.LONG, entered(m), D(95), D(110))

        assert hit.reason is ExitReason.SQUARE_OFF


class TestSwingExit:
    def sim(self) -> tuple[FakeMarket, ExitSimulator]:
        m = market()
        m.put_day(X, MON, flat_day(X, MON, 100))
        return m, ExitSimulator(m, EXEC)

    def test_the_entry_day_is_watched_to_the_close_not_only_to_1515(self) -> None:
        m, sim = self.sim()
        m.put_day(X, MON, replace_bar(flat_day(X, MON, 100), 15, 20, h=112, low=100, c=111))

        hit = sim.swing_exit(X, Side.LONG, entered(m), D(95), D(110), 5)

        assert (hit.reason, hit.price) == (ExitReason.TARGET, D(110))

    def test_later_sessions_are_watched_on_daily_bars_stop_first_and_gap_at_the_open(self) -> None:
        m, sim = self.sim()
        for d, args in (
            (TUE, (100, 101, 99, 100)),
            (WED, (100, 112, 94, 100)),
            (THU, (100, 101, 99, 100)),
        ):
            m.put_daily(X, daily(d, *args))
        hit = sim.swing_exit(X, Side.LONG, entered(m), D(95), D(110), 3)
        assert (hit.reason, hit.price, hit.ts) == (ExitReason.STOP, D(95), at(WED, 15, 30))

        m.put_daily(X, daily(WED, 90, 91, 89, 90))  # opens beyond the stop
        assert sim.swing_exit(X, Side.LONG, entered(m), D(95), D(110), 3).price == D(90)

    def test_no_touch_exits_at_the_close_of_the_session_hold_days_after_entry(self) -> None:
        m, sim = self.sim()
        for d, close in ((TUE, 101), (WED, 102), (THU, 103), (FRI, 104)):
            m.put_daily(X, daily(d, 100, 105, 99, close))

        hit = sim.swing_exit(X, Side.LONG, entered(m), D(90), D(120), 3)

        assert (hit.reason, hit.price, hit.ts) == (ExitReason.TIME, D(103), at(THU, 15, 30))

    def test_a_short_targets_below_and_stops_above_on_daily_bars(self) -> None:
        m, sim = self.sim()
        m.put_daily(X, daily(TUE, 100, 101, 88, 90))
        fill = EntryFiller(m, EXEC).fill(X, Side.SHORT, at(MON, 10, 2), 10)
        assert isinstance(fill, EntryFill)

        hit = sim.swing_exit(X, Side.SHORT, fill, D(110), D(90), 3)

        assert (hit.reason, hit.price) == (ExitReason.TARGET, D(90))

    def test_the_end_of_the_data_closes_at_the_last_known_close(self) -> None:
        m, sim = self.sim()
        m.put_daily(X, daily(TUE, 100, 101, 99, 100.5))

        hit = sim.swing_exit(X, Side.LONG, entered(m), D(90), D(120), 5)

        assert hit.reason is ExitReason.END_OF_DATA and hit.price == D("100.5")

    def test_a_daily_bar_must_be_consistent(self) -> None:
        with pytest.raises(ValueError, match="within its range"):
            daily(TUE, 100, 99, 101, 100)
