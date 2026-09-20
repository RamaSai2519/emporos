"""EM-101: the bar clock and the forward-only feed."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from emporos.backtest.clock import BarClock, TimeTravelError
from emporos.backtest.feed import (
    ClosedBarFeed,
    FeedConsumedError,
    FeedError,
    FeedWindow,
    WarmupLoader,
)
from emporos.domain.candles import Timeframe
from tests.support.backtest import InMemoryCandles, trading_days
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0, bar_at, closes_to_bars

DAY = timedelta(days=1)
CLOSES = ["100", "101", "102", "103", "104", "105"]


def feed_over(
    bars: list, ids: tuple[str, ...] = (INSTRUMENT,), days: int = 3, chunk: timedelta = 30 * DAY
) -> tuple[ClosedBarFeed, InMemoryCandles]:
    reader = InMemoryCandles(bars)
    return ClosedBarFeed(reader, ids, Timeframe.M5, FeedWindow(T0, T0 + days * DAY), chunk), reader


async def drain(feed: ClosedBarFeed) -> list:
    return [bar async for bar in feed]


class TestBarClock:
    def test_it_moves_forward_and_may_stand_still(self) -> None:
        clock = BarClock(T0)
        clock.set(T0 + timedelta(minutes=5))
        clock.set(T0 + timedelta(minutes=5))
        assert clock.now() == T0 + timedelta(minutes=5)

    def test_it_refuses_to_go_back(self) -> None:
        clock = BarClock(T0 + timedelta(minutes=5))
        with pytest.raises(TimeTravelError):
            clock.set(T0)
        assert clock.now() == T0 + timedelta(minutes=5)

    def test_it_needs_utc(self) -> None:
        with pytest.raises(ValueError):
            BarClock(datetime(2026, 1, 5, 9, 15))
        with pytest.raises(ValueError):
            BarClock(T0).set(datetime(2026, 1, 5, 9, 15))

    def test_the_view_can_read_and_nothing_else(self) -> None:
        clock = BarClock(T0)
        view = clock.view()
        assert view.now() == T0
        clock.set(T0 + DAY)
        assert view.now() == T0 + DAY
        public = {name for name in dir(view) if not name.startswith("_")}
        assert public == {"now"}


class TestFeedOrderAndBounds:
    async def test_bars_come_in_close_order_with_instruments_interleaved(self) -> None:
        bars = closes_to_bars(CLOSES, INSTRUMENT) + closes_to_bars(CLOSES, OTHER_INSTRUMENT)
        feed, _ = feed_over(bars, (INSTRUMENT, OTHER_INSTRUMENT))

        served = await drain(feed)

        assert [(b.ts, b.instrument_id) for b in served] == sorted(
            [(b.ts, b.instrument_id) for b in bars], key=lambda k: (k[0], k[1] != INSTRUMENT)
        )
        assert len(served) == 12

    async def test_the_window_is_half_open_on_bar_open_time(self) -> None:
        bars = trading_days(4, CLOSES)
        feed, _ = feed_over(bars, days=2)

        served = await drain(feed)

        assert {b.ts.date() for b in served} == {T0.date(), (T0 + DAY).date()}
        assert len(served) == 12

    async def test_a_bar_that_had_not_closed_by_the_window_end_is_not_served(self) -> None:
        bars = closes_to_bars(CLOSES, INSTRUMENT)
        reader = InMemoryCandles(bars)
        end = bars[3].ts + timedelta(minutes=2)  # the fourth bar closes 3 minutes later
        feed = ClosedBarFeed(reader, (INSTRUMENT,), Timeframe.M5, FeedWindow(T0, end))

        served = await drain(feed)

        assert [b.ts for b in served] == [b.ts for b in bars[:3]]

    async def test_chunk_size_changes_nothing_about_what_is_served(self) -> None:
        bars = trading_days(9, CLOSES) + trading_days(9, CLOSES, OTHER_INSTRUMENT)
        ids = (INSTRUMENT, OTHER_INSTRUMENT)
        whole, _ = feed_over(bars, ids, days=9)
        small, _ = feed_over(bars, ids, days=9, chunk=timedelta(hours=7))

        assert await drain(whole) == await drain(small)


class TestFeedIsNotAFrame:
    async def test_it_can_be_read_once(self) -> None:
        feed, _ = feed_over(closes_to_bars(CLOSES))
        await drain(feed)
        with pytest.raises(FeedConsumedError):
            await drain(feed)

    def test_it_offers_no_way_to_ask_for_a_bar_by_position_or_time(self) -> None:
        feed, _ = feed_over(closes_to_bars(CLOSES))

        for name in ("__getitem__", "__len__", "__iter__", "bars", "get", "at", "peek", "seek"):
            assert not hasattr(feed, name), name
        with pytest.raises(TypeError):
            feed[0]  # type: ignore[index]

    async def test_bars_beyond_the_chunk_being_served_have_not_been_read(self) -> None:
        bars = trading_days(6, CLOSES)
        feed, reader = feed_over(bars, days=6, chunk=DAY)

        iterator = feed.__aiter__()
        first = await anext(iterator)

        assert first.ts == bars[0].ts
        assert [(start, end) for _, _, start, end in reader.reads] == [(T0, T0 + DAY)]

    async def test_nothing_is_read_before_iteration_starts(self) -> None:
        _, reader = feed_over(closes_to_bars(CLOSES))
        assert reader.reads == []


class TestFeedRefusesBadData:
    async def test_a_bar_for_another_instrument(self) -> None:
        class Lying(InMemoryCandles):
            async def get_range(self, instrument_id, timeframe, start, end):  # type: ignore[no-untyped-def]
                return [bar_at(OTHER_INSTRUMENT, 0)]

        liar = ClosedBarFeed(Lying([]), (INSTRUMENT,), Timeframe.M5, FeedWindow(T0, T0 + DAY))
        with pytest.raises(FeedError, match="got NSE:1002"):
            await drain(liar)

    async def test_a_bar_of_another_timeframe(self) -> None:
        class Lying(InMemoryCandles):
            async def get_range(self, instrument_id, timeframe, start, end):  # type: ignore[no-untyped-def]
                return [bar_at(INSTRUMENT, 0, timeframe=Timeframe.M15)]

        feed = ClosedBarFeed(Lying([]), (INSTRUMENT,), Timeframe.M5, FeedWindow(T0, T0 + DAY))
        with pytest.raises(FeedError):
            await drain(feed)

    async def test_a_bar_outside_the_range_that_was_asked_for(self) -> None:
        class Lying(InMemoryCandles):
            async def get_range(self, instrument_id, timeframe, start, end):  # type: ignore[no-untyped-def]
                return [bar_at(INSTRUMENT, 60 * 24 * 5)]

        feed = ClosedBarFeed(Lying([]), (INSTRUMENT,), Timeframe.M5, FeedWindow(T0, T0 + DAY))
        with pytest.raises(FeedError, match="outside the range"):
            await drain(feed)

    async def test_two_bars_at_one_timestamp(self) -> None:
        class Lying(InMemoryCandles):
            async def get_range(self, instrument_id, timeframe, start, end):  # type: ignore[no-untyped-def]
                return [bar_at(INSTRUMENT, 0), bar_at(INSTRUMENT, 0, close="101")]

        feed = ClosedBarFeed(Lying([]), (INSTRUMENT,), Timeframe.M5, FeedWindow(T0, T0 + DAY))
        with pytest.raises(FeedError, match="two bars"):
            await drain(feed)


class TestFeedConstruction:
    def test_it_needs_instruments_named_once(self) -> None:
        window = FeedWindow(T0, T0 + DAY)
        for ids in ((), (INSTRUMENT, INSTRUMENT)):
            with pytest.raises(ValueError):
                ClosedBarFeed(InMemoryCandles([]), ids, Timeframe.M5, window)

    def test_it_needs_a_positive_chunk(self) -> None:
        with pytest.raises(ValueError):
            ClosedBarFeed(
                InMemoryCandles([]), (INSTRUMENT,), Timeframe.M5, FeedWindow(T0, T0 + DAY),
                timedelta(0),
            )  # fmt: skip

    def test_a_window_needs_utc_and_a_forward_span(self) -> None:
        with pytest.raises(ValueError):
            FeedWindow(datetime(2026, 1, 5), T0)
        with pytest.raises(ValueError):
            FeedWindow(T0, T0)
        with pytest.raises(ValueError):
            FeedWindow(T0, datetime(2026, 1, 6))


class TestWarmup:
    async def test_it_returns_the_last_bars_that_had_closed_and_nothing_later(self) -> None:
        bars = closes_to_bars([str(100 + i) for i in range(20)])
        moment = bars[10].ts  # bars 0..9 closed by now; bar 10 opens exactly now
        loader = WarmupLoader(InMemoryCandles(bars), (INSTRUMENT,), Timeframe.M5, 4, 5 * DAY)

        warm = await loader.load(moment)

        assert [b.ts for b in warm] == [b.ts for b in bars[6:10]]
        assert all(b.closes_at <= moment for b in warm)

    async def test_a_bar_still_forming_at_the_moment_is_left_out(self) -> None:
        bars = closes_to_bars([str(100 + i) for i in range(20)])
        moment = bars[10].ts + timedelta(minutes=2)
        loader = WarmupLoader(InMemoryCandles(bars), (INSTRUMENT,), Timeframe.M5, 50, 5 * DAY)

        warm = await loader.load(moment)

        assert warm[-1].ts == bars[9].ts

    async def test_depth_is_per_instrument(self) -> None:
        bars = closes_to_bars(CLOSES, INSTRUMENT) + closes_to_bars(CLOSES, OTHER_INSTRUMENT)
        loader = WarmupLoader(
            InMemoryCandles(bars), (INSTRUMENT, OTHER_INSTRUMENT), Timeframe.M5, 2, DAY
        )

        warm = await loader.load(T0 + timedelta(minutes=30))

        assert sorted(b.instrument_id for b in warm) == [INSTRUMENT] * 2 + [OTHER_INSTRUMENT] * 2

    async def test_zero_depth_reads_nothing(self) -> None:
        reader = InMemoryCandles(closes_to_bars(CLOSES))
        loader = WarmupLoader(reader, (INSTRUMENT,), Timeframe.M5, 0, DAY)
        assert await loader.load(T0 + DAY) == ()
        assert reader.reads == []

    def test_it_validates_its_arguments(self) -> None:
        reader = InMemoryCandles([])
        with pytest.raises(ValueError):
            WarmupLoader(reader, (INSTRUMENT,), Timeframe.M5, -1, DAY)
        with pytest.raises(ValueError):
            WarmupLoader(reader, (INSTRUMENT,), Timeframe.M5, 1, timedelta(0))

    async def test_the_moment_must_be_utc(self) -> None:
        loader = WarmupLoader(InMemoryCandles([]), (INSTRUMENT,), Timeframe.M5, 1, DAY)
        with pytest.raises(ValueError):
            await loader.load(datetime(2026, 1, 5))
