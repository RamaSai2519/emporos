"""EM-108: backfill one derived timeframe without storing the 1m bars underneath it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.history.derived import DerivedBarBackfill
from emporos.marketdata.session import SessionWindow
from tests.support.fakes import InMemoryCandleStore, make_instrument
from tests.support.history import BrokerHistory, bar

SBIN, RELIANCE = make_instrument("3045"), make_instrument("2885")
FIRST, LAST = date(2026, 8, 3), date(2026, 9, 18)  # Mon .. Fri: 47 calendar days, 35 weekdays
WINDOW = SessionWindow()
EVERYTHING = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))


class Rig:
    def __init__(
        self, history: BrokerHistory | None = None, keep: Timeframe = Timeframe.M5
    ) -> None:
        self.history = history or BrokerHistory()
        self.store = InMemoryCandleStore()
        self.backfill = DerivedBarBackfill(self.history, self.store, WINDOW, keep)

    async def stored(self, timeframe: Timeframe, instrument=SBIN) -> list[Candle]:  # type: ignore[no-untyped-def]
        return await self.store.read(instrument.instrument_id, timeframe, *EVERYTHING)


async def test_only_the_wanted_timeframe_is_stored_never_the_1m_bars() -> None:
    rig = Rig()

    report = await rig.backfill.run([SBIN], FIRST, LAST)

    assert report.ok and report.bars_written == 35 * 75
    assert len(await rig.stored(Timeframe.M5)) == 35 * 75
    assert await rig.stored(Timeframe.M1) == [] and await rig.stored(Timeframe.M15) == []


async def test_a_five_minute_bar_is_folded_from_its_minutes_by_hand() -> None:
    # the test broker prices a minute at 100 + (its UTC minute)/100, one price per minute
    # (o = h = l = c). The session opens 09:15 IST = 03:45 UTC, so the first five minutes
    # 03:45..03:49 carry 100.45, 100.46, 100.47, 100.48, 100.49
    rig = Rig()
    await rig.backfill.run([SBIN], FIRST, FIRST)

    first = (await rig.stored(Timeframe.M5))[0]

    assert first.ts.astimezone(IST).strftime("%H:%M") == "09:15"  # the bar is stamped at its OPEN
    assert (first.open.amount, first.high.amount, first.low.amount, first.close.amount) == (
        Decimal("100.45"), Decimal("100.49"), Decimal("100.45"), Decimal("100.49"),
    )  # fmt: skip
    assert first.volume == 50 and first.partial is False  # 5 minutes of 10


async def test_a_long_window_is_fetched_in_chunks_within_the_brokers_limit() -> None:
    rig = Rig()

    report = await rig.backfill.run([SBIN], FIRST, LAST)

    spans = [(end - start).days for _, start, end in rig.history.requests]
    assert report.chunks_fetched == len(rig.history.requests) == 2  # 28 + 19 calendar days
    assert all(days <= 30 for days in spans)


async def test_each_instrument_is_fetched_in_turn_and_the_days_are_counted() -> None:
    rig = Rig()

    report = await rig.backfill.run([SBIN, RELIANCE], FIRST, FIRST + timedelta(days=6))

    assert [name for name, _, _ in rig.history.requests] == [
        SBIN.instrument_id,
        RELIANCE.instrument_id,
    ]
    assert report.bars_per_day[(SBIN.instrument_id, FIRST)] == 75
    assert report.bars_per_day[(RELIANCE.instrument_id, date(2026, 8, 7))] == 75
    assert (SBIN.instrument_id, date(2026, 8, 8)) not in report.bars_per_day  # a Saturday


async def test_a_minute_the_broker_left_out_makes_that_bar_partial_not_missing() -> None:
    rig = Rig(BrokerHistory(silent=lambda ts: ts.astimezone(IST).strftime("%H:%M") == "10:07"))
    await rig.backfill.run([SBIN], FIRST, FIRST)

    by_time = {b.ts.astimezone(IST).strftime("%H:%M"): b for b in await rig.stored(Timeframe.M5)}

    assert by_time["10:05"].partial is True and by_time["10:05"].volume == 40
    assert by_time["10:10"].partial is False


async def test_a_failed_chunk_is_reported_and_the_rest_still_runs() -> None:
    history = BrokerHistory()
    history.fail_requests = {1}
    rig = Rig(history)

    report = await rig.backfill.run([SBIN], FIRST, LAST)

    assert not report.ok and report.failed_chunks == [
        (SBIN.instrument_id, FIRST, date(2026, 8, 30))
    ]
    assert report.chunks_fetched == 1
    stored_days = {b.ts.astimezone(IST).date() for b in await rig.stored(Timeframe.M5)}
    assert min(stored_days) > date(2026, 8, 30)  # the first chunk left nothing behind


async def test_running_it_twice_changes_nothing() -> None:
    rig = Rig()
    await rig.backfill.run([SBIN], FIRST, FIRST + timedelta(days=4))
    before = await rig.stored(Timeframe.M5)

    await rig.backfill.run([SBIN], FIRST, FIRST + timedelta(days=4))

    assert await rig.stored(Timeframe.M5) == before


async def test_bars_outside_the_session_are_dropped() -> None:
    class Noisy(BrokerHistory):
        async def fetch(self, instrument, timeframe, start, end):  # type: ignore[no-untyped-def]
            regular = await super().fetch(instrument, timeframe, start, end)
            opens = WINDOW.open_at(FIRST)
            extras = [
                bar(instrument, opens - timedelta(minutes=1)),
                bar(instrument, WINDOW.close_at(FIRST)),
            ]
            return [*extras, *regular]

    rig = Rig(Noisy())
    await rig.backfill.run([SBIN], FIRST, FIRST)

    times = {b.ts.astimezone(IST).strftime("%H:%M") for b in await rig.stored(Timeframe.M5)}
    assert len(times) == 75 and "09:10" not in times and "15:30" not in times


class TestConfiguration:
    def test_only_derived_timeframes_can_be_kept(self) -> None:
        for bad in (Timeframe.M1, Timeframe.D1):
            with pytest.raises(ConfigurationError):
                Rig(keep=bad)

    def test_the_chunk_size_is_within_the_brokers_limit(self) -> None:
        for days in (0, 31):
            with pytest.raises(ConfigurationError):
                DerivedBarBackfill(
                    BrokerHistory(), InMemoryCandleStore(), WINDOW, Timeframe.M5, days
                )

    async def test_the_window_must_run_forward(self) -> None:
        with pytest.raises(ConfigurationError):
            await Rig().backfill.run([SBIN], LAST, FIRST)
