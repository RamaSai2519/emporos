"""EM-226: the regime conditions, entry filters, conviction depth and the listed-strike put spread,
each pinned on series and chains small enough to check by hand."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from emporos.options.listed_strikes import RiskCappedPutSpread
from emporos.options.regime import (
    AllOf,
    ConditionFilter,
    ConvictionDepth,
    FirstSessionOfWeek,
    NoEventThroughExpiry,
    SmaStack,
    TrailingPercentileBand,
    TrendAboveSma,
    VixVolatility,
)
from emporos.options.spread import Vertical
from tests.support.option_chains import DAY0, PUT, days_after, snapshot

D = Decimal
FIRST = date(2026, 1, 1)


def series(*values: int) -> dict[date, Decimal]:
    return {FIRST + timedelta(days=i): D(v) for i, v in enumerate(values)}


def day(n: int) -> date:
    return FIRST + timedelta(days=n)


class TestTrend:
    def test_the_close_above_its_average_including_today_holds(self) -> None:
        trend = TrendAboveSma(series(10, 11, 12, 13, 20), window=3)

        assert trend.holds(day(4))  # 20 > mean(12, 13, 20) = 15
        assert trend.holds(day(3))  # 13 > mean(11, 12, 13) = 12

    def test_a_falling_close_or_a_short_history_or_an_unknown_day_does_not_hold(self) -> None:
        trend = TrendAboveSma(series(20, 13, 12, 11, 10), window=3)

        assert not trend.holds(day(4))  # 10 < mean(12, 11, 10)
        assert not TrendAboveSma(series(10, 11, 12), window=4).holds(day(2))  # needs 4 sessions
        assert not trend.holds(day(40))

    def test_equal_to_the_average_is_not_above_it(self) -> None:
        assert not TrendAboveSma(series(10, 10, 10), window=3).holds(day(2))

    def test_a_window_needs_two_sessions(self) -> None:
        with pytest.raises(ValueError):
            TrendAboveSma({}, window=1)


class TestSmaStack:
    def test_close_over_the_fast_average_over_the_slow_one(self) -> None:
        stack = SmaStack(series(1, 2, 3, 4, 5, 6), fast=2, slow=4)

        assert stack.holds(day(5))  # 6 > mean(5, 6) = 5.5 > mean(3, 4, 5, 6) = 4.5
        assert not stack.holds(day(2))  # fewer than four sessions

    def test_a_close_under_a_rising_fast_average_does_not_hold(self) -> None:
        assert not SmaStack(series(1, 2, 3, 9, 5, 4), fast=2, slow=4).holds(day(5))

    def test_the_averages_must_be_ordered(self) -> None:
        with pytest.raises(ValueError):
            SmaStack({}, fast=5, slow=5)


class TestPercentileBand:
    def band(self, *values: int) -> TrailingPercentileBand:
        return TrailingPercentileBand(series(*values), D("0.2"), D("0.8"), lookback=10)

    def test_a_value_between_the_quantiles_of_the_previous_sessions_holds(self) -> None:
        # the previous ten are 1..10: the 20th percentile is 2 and the 80th is 8 (nearest rank)
        base = list(range(1, 11))

        assert self.band(*base, 5).holds(day(10))
        assert self.band(*base, 2).holds(day(10)) and self.band(*base, 8).holds(day(10))
        assert not self.band(*base, 1).holds(day(10)) and not self.band(*base, 9).holds(day(10))

    def test_today_is_not_part_of_its_own_baseline(self) -> None:
        # if today's 100 were in the baseline it would lift the 80th percentile to 10 (holding)
        assert not self.band(*range(1, 11), 100).holds(day(10))

    def test_it_stays_false_until_a_full_lookback_exists(self) -> None:
        assert not self.band(*range(1, 11)).holds(day(9))

    def test_the_bounds_must_be_ordered(self) -> None:
        with pytest.raises(ValueError):
            TrailingPercentileBand({}, D("0.8"), D("0.2"))
        with pytest.raises(ValueError):
            TrailingPercentileBand({}, D("0.2"), D("0.8"), lookback=1)


class TestFiltersAndDepth:
    def test_all_of_needs_every_condition(self) -> None:
        up = TrendAboveSma(series(1, 2, 3), window=2)
        down = TrendAboveSma(series(3, 2, 1), window=2)

        assert AllOf((up,)).holds(day(2)) and not AllOf((up, down)).holds(day(2))

    def test_a_condition_becomes_an_entry_filter(self) -> None:
        snap = snapshot(day(2), "100", day(40), {})

        assert ConditionFilter(TrendAboveSma(series(1, 2, 3), window=2)).allows(snap, day(40))
        assert not ConditionFilter(TrendAboveSma(series(3, 2, 1), window=2)).allows(snap, day(40))

    def test_only_the_first_session_of_each_week_is_open(self) -> None:
        sessions = [date(2026, 1, d) for d in (5, 6, 9, 13, 14)]  # week of the 12th starts on 13th
        opening = FirstSessionOfWeek(sessions)

        def allows(d: int) -> bool:
            return opening.allows(
                snapshot(date(2026, 1, d), "100", date(2026, 3, 1), {}), date(2026, 3, 1)
            )

        assert [allows(d) for d in (5, 6, 9, 13, 14)] == [True, False, False, True, False]

    def test_an_event_between_entry_and_expiry_refuses_the_spread(self) -> None:
        events = NoEventThroughExpiry([date(2026, 1, 20)])
        today = snapshot(date(2026, 1, 5), "100", date(2026, 1, 30), {})

        assert not events.allows(today, date(2026, 1, 30))  # inside the window
        assert events.allows(today, date(2026, 1, 19))  # expires the day before the event
        assert not events.allows(today, date(2026, 1, 20))  # the expiry day itself counts
        on_the_day = snapshot(date(2026, 1, 20), "100", date(2026, 2, 27), {})
        assert not events.allows(on_the_day, date(2026, 2, 27))  # entry day counts
        after = snapshot(date(2026, 1, 21), "100", date(2026, 2, 27), {})
        assert events.allows(after, date(2026, 2, 27))

    def test_the_extra_slot_opens_only_when_the_conviction_holds(self) -> None:
        depth = ConvictionDepth(2, 1, TrendAboveSma(series(1, 2, 3, 2), window=2))

        assert depth.limit(snapshot(day(2), "100", day(40), {})) == 3  # rising
        assert depth.limit(snapshot(day(3), "100", day(40), {})) == 2  # falling

    def test_a_depth_needs_a_base_slot(self) -> None:
        with pytest.raises(ValueError):
            ConvictionDepth(0, 1, TrendAboveSma({}, 2))

    def test_the_vix_close_is_the_volatility_as_a_fraction(self) -> None:
        vix = VixVolatility({day(0): D(15), day(1): D(0)})

        assert vix.annual_volatility(day(0)) == D("0.15")
        assert vix.annual_volatility(day(1)) is None and vix.annual_volatility(day(9)) is None


class Fixed:
    def __init__(self, vol: str | None) -> None:
        self._vol = None if vol is None else D(vol)

    def annual_volatility(self, day: date) -> Decimal | None:
        return self._vol


EXPIRY = DAY0.replace(year=2027)  # exactly 365 days on: one standard deviation is spot x vol


def puts(*strikes: int) -> dict[tuple[int, object], str]:
    return {(s, PUT): "1" for s in strikes}


class TestListedPutSpread:
    def build(
        self,
        strikes: list[int],
        *,
        vol: str | None = "0.10",
        k: str = "1",
        cap: str = "5000",
        lot: int = 25,
    ) -> Vertical | None:
        chain = puts(*strikes)
        snap = snapshot(DAY0, "25000", EXPIRY, chain, lot_size=lot)  # type: ignore[arg-type]
        plan = RiskCappedPutSpread(Fixed(vol), D(k), D(cap)).build(snap, EXPIRY)
        return None if plan is None else plan.verticals[0]

    def test_the_short_is_the_listed_strike_at_or_below_the_target_and_the_long_the_widest_in_cap(
        self,
    ) -> None:
        # target = 25000 - 1 x 2,500 = 22,500; a lot of 25 and a cap of 5,000 allow 200 points
        vertical = self.build([22000, 22300, 22400, 22450, 22500, 22550])

        assert (vertical.short_strike, vertical.long_strike) == (D(22500), D(22300))  # type: ignore[union-attr]

    def test_the_short_is_at_or_below_the_target_never_above(self) -> None:
        vertical = self.build([22300, 22400, 22550])  # nothing at 22,500: the next one down

        assert vertical is not None and vertical.short_strike == D(22400)

    def test_the_wing_is_never_wider_than_the_cap_allows(self) -> None:
        vertical = self.build([22500, 22250, 22299, 22301], cap="5000")  # 200 points at most

        assert vertical is not None and vertical.long_strike == D(22301)  # 22299 is 201 wide

    def test_a_bigger_lot_narrows_the_wing(self) -> None:
        vertical = self.build([22000, 22300, 22400, 22450, 22500], lot=75)  # 5000 / 75 = 66.67

        assert vertical is not None and vertical.long_strike == D(22450)

    def test_a_larger_multiple_moves_the_short_further_out(self) -> None:
        vertical = self.build([21000, 21200, 22500, 22700], k="1.5")  # target 21,250

        assert vertical is not None and vertical.short_strike == D(21200)

    def test_no_strike_below_the_target_or_no_wing_in_the_cap_means_no_spread(self) -> None:
        assert self.build([23000, 24000]) is None  # nothing at or below 22,500
        assert self.build([22500, 22000]) is None  # the only wing is 500 wide, over the cap

    def test_no_volatility_or_no_chain_means_no_spread(self) -> None:
        assert self.build([22400, 22500], vol=None) is None
        snap = snapshot(DAY0, "25000", EXPIRY, puts(22400, 22500))
        assert RiskCappedPutSpread(Fixed("0.1"), D(1), D(5000)).build(snap, days_after(9)) is None

    def test_nonsense_settings_are_refused(self) -> None:
        with pytest.raises(ValueError):
            RiskCappedPutSpread(Fixed("0.1"), D(0), D(5000))
        with pytest.raises(ValueError):
            RiskCappedPutSpread(Fixed("0.1"), D(1), D(0))
