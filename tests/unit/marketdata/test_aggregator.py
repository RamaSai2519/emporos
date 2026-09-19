"""EM-52: 1m aggregation on the wall clock — silent bars close on schedule, closed candles are
never rewritten, and the volume/partial rules hold."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from emporos.core.clock import IST, FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.marketdata.aggregator import MinuteCandleAggregator
from tests.support.fakes import CandleCollector, make_tick

FRIDAY = date(2026, 9, 18)


def ist(hour: int, minute: int, second: int = 0, micro: int = 0, day: date = FRIDAY) -> datetime:
    return datetime(
        day.year, day.month, day.day, hour, minute, second, micro, tzinfo=IST
    ).astimezone(UTC)


class Rig:
    def __init__(self, grace: timedelta = timedelta(seconds=1)) -> None:
        self.clock = FixedClock(ist(9, 0))
        self.aggregator = MinuteCandleAggregator(self.clock, grace=grace)
        self.out = CandleCollector()
        self.aggregator.subscribe(self.out)

    def tick(self, at: datetime, ltp: str = "100.00", **kwargs: object) -> None:
        self.clock.set(max(at, self.clock.now()))
        self.aggregator.on_tick(make_tick(at, ltp, **kwargs))  # type: ignore[arg-type]

    def advance_to(self, at: datetime) -> None:
        self.clock.set(at)
        self.aggregator.advance(at)

    @property
    def m1(self) -> list[Candle]:
        return self.out.of(Timeframe.M1)


def price(candle: Candle) -> tuple[str, str, str, str]:
    return tuple(str(p.amount) for p in (candle.open, candle.high, candle.low, candle.close))  # type: ignore[return-value]


def test_ticks_build_ohlc_and_the_bar_closes_only_when_the_clock_says_so() -> None:
    rig = Rig()
    for second, ltp in ((5, "100.00"), (20, "101.50"), (35, "99.75"), (50, "100.25")):
        rig.tick(ist(10, 0, second), ltp)

    assert rig.m1 == []  # ticks alone never close a bar in their own minute

    rig.advance_to(ist(10, 1, 1))
    (bar,) = rig.m1
    assert bar.ts == ist(10, 0) and bar.timeframe is Timeframe.M1
    assert price(bar) == ("100.00", "101.50", "99.75", "100.25")


def test_the_bar_closes_exactly_at_the_boundary_plus_grace_not_a_moment_before() -> None:
    rig = Rig(grace=timedelta(seconds=1))
    rig.tick(ist(10, 0, 30))

    rig.advance_to(ist(10, 1, 0, 999_999))
    assert rig.m1 == []
    rig.advance_to(ist(10, 1, 1))
    assert len(rig.m1) == 1


def test_a_tick_stamped_in_the_last_second_but_arriving_within_grace_still_lands_in_the_bar() -> (
    None
):
    rig = Rig()
    rig.tick(ist(10, 0, 10), "100.00")

    rig.clock.set(ist(10, 1, 0, 500_000))  # the boundary has passed on the wall clock...
    rig.aggregator.advance()  # ...but the grace keeps the bar open
    rig.aggregator.on_tick(make_tick(ist(10, 0, 59, 800_000), "100.90"))  # late-arriving, in-minute
    rig.advance_to(ist(10, 1, 1))

    assert price(rig.m1[0])[3] == "100.90"


def test_a_silent_instrument_gets_flat_zero_volume_bars_exactly_on_schedule() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 10), "250.00", volume=1_000)

    closed_at: list[tuple[str, int]] = []
    for minute in range(1, 5):
        before = len(rig.m1)
        rig.advance_to(ist(10, minute, 0, 999_999))
        assert len(rig.m1) == before  # not a moment early
        rig.advance_to(ist(10, minute, 1))
        closed_at.append((f"10:{minute - 1:02d}", len(rig.m1)))

    assert [c[1] for c in closed_at] == [1, 2, 3, 4]  # one bar per elapsed minute
    flats = rig.m1[1:]
    assert [c.ts for c in flats] == [ist(10, m) for m in (1, 2, 3)]
    assert all(price(c) == ("250.00",) * 4 and c.volume == 0 and not c.partial for c in flats)


def test_an_instrument_that_has_never_ticked_gets_no_bars() -> None:
    rig = Rig()
    rig.advance_to(ist(10, 30))
    assert rig.m1 == []


def test_bars_stop_at_the_session_close_and_nothing_is_fabricated_overnight() -> None:
    rig = Rig()
    rig.tick(ist(15, 20), "100.00")

    rig.advance_to(ist(18, 0))

    assert [c.ts for c in rig.m1][-1] == ist(15, 29)  # the last in-session minute
    assert len(rig.m1) == 10  # 15:20 .. 15:29


def test_a_new_day_starts_clean_and_yesterdays_tail_is_closed_first() -> None:
    rig = Rig()
    rig.tick(ist(15, 27), "100.00")
    monday = date(2026, 9, 21)

    rig.tick(ist(9, 20, day=monday), "105.00")  # first tick of the next session

    yesterday = [c for c in rig.m1 if c.ts.astimezone(IST).date() == FRIDAY]
    assert [c.ts for c in yesterday][-1] == ist(15, 29)  # 15:27 real, 15:28-15:29 flat
    rig.advance_to(ist(9, 22, day=monday))
    today = [c for c in rig.m1 if c.ts.astimezone(IST).date() == monday]
    assert [c.ts for c in today] == [ist(9, 20, day=monday)]  # no fabricated 09:15-09:19 bars


def test_a_tick_for_an_already_closed_minute_never_rewrites_the_candle() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 10), "100.00")
    rig.tick(ist(10, 1, 10), "101.00")  # closes 10:00 (a later minute began)
    (closed,) = rig.m1
    snapshot = (closed.open, closed.high, closed.low, closed.close, closed.volume)

    rig.aggregator.on_tick(make_tick(ist(10, 0, 40), "150.00"))  # far outside, and late

    assert rig.m1 == [closed]  # no re-emission, no change
    assert (closed.open, closed.high, closed.low, closed.close, closed.volume) == snapshot
    assert rig.aggregator.stats.late_ticks_dropped == 1


def test_an_out_of_order_tick_may_widen_the_open_bar_but_never_change_open_or_close() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 10), "100.00")
    rig.tick(ist(10, 0, 30), "101.00")
    rig.aggregator.on_tick(make_tick(ist(10, 0, 20), "103.00", out_of_order=True))  # a new high
    rig.aggregator.on_tick(make_tick(ist(10, 0, 15), "98.00", out_of_order=True))  # a new low
    rig.aggregator.on_tick(make_tick(ist(10, 0, 12), "100.50", out_of_order=True))  # inside range

    rig.advance_to(ist(10, 1, 1))

    assert price(rig.m1[0]) == ("100.00", "103.00", "98.00", "101.00")
    assert rig.aggregator.stats.widened_by_late_tick == 2


def test_an_out_of_order_tick_for_a_minute_that_is_not_open_is_dropped() -> None:
    rig = Rig()
    rig.tick(ist(10, 1, 30), "100.00")  # 10:01 is open; 10:00 was never seen
    rig.aggregator.on_tick(make_tick(ist(10, 0, 30), "100.00", out_of_order=True))
    assert rig.aggregator.stats.late_ticks_dropped == 1


def test_volume_is_the_change_in_the_cumulative_day_volume() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 5), "100.00", volume=1_000)  # baseline established here
    rig.tick(ist(10, 0, 30), "100.10", volume=1_400)
    rig.tick(ist(10, 0, 50), "100.20", volume=1_900)
    rig.tick(ist(10, 1, 20), "100.30", volume=2_000)

    rig.advance_to(ist(10, 2, 1))

    first, second = rig.m1
    assert first.volume == 900 and second.volume == 100  # 1900-1000, then 2000-1900


def test_the_first_bar_without_a_volume_baseline_is_partial_and_later_ones_are_not() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 5), "100.00", volume=5_000_000)  # subscribed mid-session
    rig.tick(ist(10, 1, 5), "100.00", volume=5_000_300)
    rig.advance_to(ist(10, 3, 1))

    assert [(c.partial, c.volume) for c in rig.m1] == [(True, 0), (False, 300), (False, 0)]


def test_a_session_started_from_the_open_has_a_full_baseline_and_is_not_partial() -> None:
    rig = Rig()
    rig.tick(ist(9, 15, 0, 200_000), "100.00", volume=120_000)  # opening auction volume included
    rig.advance_to(ist(9, 17, 0))

    assert rig.m1[0].volume == 120_000 and rig.m1[0].partial is False


def test_ltp_mode_ticks_have_no_volume_and_are_not_flagged_for_it() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 5), "100.00", volume=None)
    rig.advance_to(ist(10, 1, 1))
    assert rig.m1[0].volume == 0 and rig.m1[0].partial is False


def test_a_later_minute_tick_closes_the_earlier_bar_and_fills_the_silent_minutes() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 10), "100.00")
    rig.tick(ist(10, 3, 10), "104.00")  # no advance() in between

    assert [c.ts for c in rig.m1] == [ist(10, 0), ist(10, 1), ist(10, 2)]
    assert [str(c.close.amount) for c in rig.m1] == ["100.00"] * 3


def test_bars_overlapping_a_feed_outage_are_flagged_partial_and_the_baseline_resets() -> None:
    rig = Rig()
    rig.tick(ist(9, 15, 5), "100.00", volume=1_000)  # from the open: full baseline
    rig.tick(ist(9, 16, 5), "100.00", volume=1_100)
    rig.clock.set(ist(9, 17, 10))
    asyncio.run(rig.aggregator.on_disconnected("dropped"))
    rig.clock.set(ist(9, 19, 30))
    asyncio.run(rig.aggregator.on_connected())
    rig.tick(ist(9, 19, 40), "101.00", volume=9_000)  # 7,900 shares traded during the outage
    rig.advance_to(ist(9, 21, 1))

    by_minute = {c.ts: c for c in rig.m1}
    assert not by_minute[ist(9, 15)].partial and not by_minute[ist(9, 16)].partial
    assert all(by_minute[ist(9, m)].partial for m in (17, 18, 19))  # inside the outage
    assert by_minute[ist(9, 19)].volume == 0  # the outage's volume did NOT pile into this bar
    assert not by_minute[ist(9, 20)].partial  # the feed is back and re-baselined


def test_instruments_are_aggregated_independently() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 5), "100.00", instrument_id="NSE:1")
    rig.tick(ist(10, 0, 6), "500.00", instrument_id="NSE:2")
    rig.advance_to(ist(10, 1, 1))

    assert {c.instrument_id: str(c.close.amount) for c in rig.m1} == {
        "NSE:1": "100.00",
        "NSE:2": "500.00",
    }


def test_a_failing_candle_subscriber_does_not_starve_the_others() -> None:
    class Exploding:
        def on_candle(self, candle: Candle) -> None:
            raise RuntimeError("consumer bug")

    rig = Rig()
    rig.aggregator.subscribe(Exploding())
    healthy = CandleCollector()
    rig.aggregator.subscribe(healthy)
    rig.tick(ist(10, 0, 5), "100.00")
    rig.advance_to(ist(10, 1, 1))

    assert len(healthy.candles) == 1


def test_identical_tick_streams_produce_identical_candles_every_time() -> None:
    def run() -> list[Candle]:
        rig = Rig()
        for i, second in enumerate(range(0, 600, 7)):
            rig.tick(
                ist(10, second // 60, second % 60),
                f"{100 + (i % 5) / 10:.2f}",
                volume=1_000 + i * 10,
            )
        rig.advance_to(ist(10, 12, 0))
        return rig.m1

    assert run() == run()


def test_a_candle_is_only_ever_emitted_once_per_minute() -> None:
    rig = Rig()
    for second in range(0, 300, 3):
        rig.tick(ist(10, second // 60, second % 60), "100.00")
    for minute in range(1, 8):
        rig.advance_to(ist(10, minute, 1))
        rig.advance_to(ist(10, minute, 30))  # calling advance again must not re-emit

    timestamps = [c.ts for c in rig.m1]
    assert len(timestamps) == len(set(timestamps))


def test_money_stays_exact() -> None:
    rig = Rig()
    rig.tick(ist(10, 0, 5), "0.10")
    rig.tick(ist(10, 0, 6), "0.30")
    rig.advance_to(ist(10, 1, 1))
    assert rig.m1[0].high == Money.of("0.30") and isinstance(
        rig.m1[0].high.amount, type(Money.of("1").amount)
    )


@pytest.mark.parametrize("bad", [ist(8, 0), ist(20, 0)])
def test_advance_outside_the_session_emits_nothing(bad: datetime) -> None:
    rig = Rig()
    rig.advance_to(bad)
    assert rig.m1 == []
