"""EM-54: after a reconnect the gap is backfilled from broker history, flagged partial, and the
broker's bars are the last writer."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from emporos.core.clock import IST, FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.marketdata.candle_writer import CandlePersister
from emporos.marketdata.recovery import ReconnectRecovery
from tests.support.fakes import (
    AdvancingSleeper,
    InMemoryCandleStore,
    RecordingAlertSink,
    make_instrument,
)

FRIDAY = date(2026, 9, 18)
GRACE = timedelta(seconds=1)
SBIN, RELIANCE = make_instrument("3045"), make_instrument("2885")


def ist(hour: int, minute: int, second: int = 0, day: date = FRIDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST).astimezone(UTC)


def broker_bar(
    instrument: Instrument, at: datetime, price: str = "100.00", volume: int = 100
) -> Candle:
    p = Money.of(price)
    return Candle(
        instrument.instrument_id,
        Timeframe.M1,
        at,
        p,
        p + Money.of("0.5"),
        p - Money.of("0.5"),
        p,
        volume,
    )


class History:
    """`CandleBackfillSource` double: one bar per minute in the requested range."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, datetime, datetime]] = []
        self.fail_for: set[str] = set()

    async def fetch_minutes(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> list[Candle]:
        self.requests.append((instrument.instrument_id, start, end))
        if instrument.instrument_id in self.fail_for:
            raise ConnectionError("history unavailable")
        minutes = int((end - start) / timedelta(minutes=1))
        return [
            broker_bar(instrument, start + timedelta(minutes=i), f"{100 + i / 10:.2f}")
            for i in range(minutes)
        ]


class Active:
    def __init__(self, *instruments: Instrument) -> None:
        self.active = tuple(instruments)


class RecordingStore(InMemoryCandleStore):
    def __init__(self) -> None:
        super().__init__()
        self.upserts: list[list[Candle]] = []

    async def upsert(self, candles: Sequence[Candle]) -> None:
        self.upserts.append(list(candles))
        await super().upsert(candles)


class Rig:
    def __init__(self, *instruments: Instrument) -> None:
        self.clock = FixedClock(ist(9, 30))
        self.sleeper = AdvancingSleeper(self.clock)
        self.history = History()
        self.store = RecordingStore()
        self.alerts = RecordingAlertSink()
        self.persister = CandlePersister(self.store)
        self.recovery = ReconnectRecovery(
            Active(*(instruments or (SBIN,))),
            self.history,
            self.store,
            self.clock,
            self.sleeper,
            GRACE,
            self.persister,
            alerts=self.alerts,
        )

    async def outage(self, down: datetime, up: datetime) -> None:
        self.clock.set(down)
        await self.recovery.on_disconnected("dropped")
        self.clock.set(up)
        await self.recovery.on_connected()
        await self.recovery.wait()

    async def stored(
        self, instrument: Instrument, timeframe: Timeframe = Timeframe.M1
    ) -> list[Candle]:
        return await self.store.read(instrument.instrument_id, timeframe, ist(9, 0), ist(16, 0))


async def test_the_first_connection_has_no_gap_and_backfills_nothing() -> None:
    rig = Rig()
    await rig.recovery.on_connected()
    await rig.recovery.wait()
    assert rig.history.requests == [] and rig.store.upserts == []


async def test_the_gap_minutes_are_backfilled_flagged_partial_and_only_they_are_written() -> None:
    rig = Rig()

    await rig.outage(ist(10, 2, 10), ist(10, 4, 30))

    minutes = await rig.stored(SBIN)
    assert [m.ts for m in minutes] == [ist(10, 2), ist(10, 3), ist(10, 4)]  # the touched minutes
    assert all(m.partial for m in minutes)  # they cover a gap
    assert len(rig.recovery.reports) == 1
    report = rig.recovery.reports[0]
    assert (report.window_start, report.window_end) == (ist(10, 2), ist(10, 5))
    assert report.recovered == (SBIN.instrument_id,) and report.failed == ()


async def test_a_straddling_higher_bar_is_built_from_the_whole_bucket_not_just_the_gap() -> None:
    rig = Rig()

    await rig.outage(ist(10, 2, 10), ist(10, 4, 30))

    (m5,) = await rig.stored(SBIN, Timeframe.M5)  # only the 10:00-10:05 bucket has closed
    assert m5.ts == ist(10, 0) and m5.partial is True
    # The history double prices minute i of the fetched range (from 09:15) at 100 + i/10, with
    # +/-0.50 wicks. The 10:00 bucket is minutes 45..49 of that range, so its OHLC comes from ALL
    # five minutes (two outside the gap), not just the three the outage touched.
    assert m5.volume == 500
    assert (m5.open, m5.high, m5.low, m5.close) == (
        Money.of("104.50"),
        Money.of("105.40"),
        Money.of("104.00"),
        Money.of("104.90"),
    )
    assert not await rig.stored(SBIN, Timeframe.M15)  # 10:00-10:15 is still open: not written
    assert not await rig.stored(SBIN, Timeframe.H1)


async def test_it_waits_for_the_reconnect_minute_and_the_brokers_bar_is_written_last() -> None:
    rig = Rig()
    stale_live_bar = broker_bar(SBIN, ist(10, 4), "999.00", volume=7)  # half a minute of data
    rig.persister.on_candle(stale_live_bar)

    await rig.outage(ist(10, 2, 10), ist(10, 4, 30))

    assert rig.sleeper.sleeps == [32.0]  # 10:04:30 -> 10:05:02 (minute + grace + 1s)
    first, second = rig.store.upserts[0], rig.store.upserts[1]
    assert first == [stale_live_bar]  # the pending live bar flushed BEFORE the backfill...
    assert any(c.ts == ist(10, 4) for c in second)
    (final,) = (m for m in await rig.stored(SBIN) if m.ts == ist(10, 4))
    assert (
        final.close != Money.of("999.00") and final.volume == 100 and final.partial
    )  # ...so the broker's wins


async def test_an_outage_outside_the_session_needs_no_backfill() -> None:
    rig = Rig()
    await rig.outage(ist(16, 0), ist(16, 5))
    assert rig.history.requests == [] and rig.recovery.reports == []


async def test_a_gap_from_a_previous_day_is_clamped_to_todays_session_open() -> None:
    rig = Rig()
    await rig.outage(ist(15, 0, day=date(2026, 9, 17)), ist(9, 20))

    report = rig.recovery.reports[0]
    assert (report.window_start, report.window_end) == (ist(9, 15), ist(9, 21))


async def test_one_instrument_failing_does_not_stop_the_others_and_alerts() -> None:
    rig = Rig(SBIN, RELIANCE)
    rig.history.fail_for = {SBIN.instrument_id}

    await rig.outage(ist(10, 2, 10), ist(10, 4, 30))

    report = rig.recovery.reports[0]
    assert report.failed == (SBIN.instrument_id,) and report.recovered == (RELIANCE.instrument_id,)
    assert await rig.stored(SBIN) == [] and len(await rig.stored(RELIANCE)) == 3
    assert [n for n, _ in rig.alerts.alerts] == ["market_data.backfill_failed"]


async def test_two_outages_are_repaired_independently() -> None:
    rig = Rig()
    await rig.outage(ist(10, 0, 10), ist(10, 1, 30))
    await rig.outage(ist(11, 0, 10), ist(11, 1, 30))
    assert [r.window_start for r in rig.recovery.reports] == [ist(10, 0), ist(11, 0)]


async def test_repeated_disconnect_events_keep_the_original_gap_start() -> None:
    rig = Rig()
    rig.clock.set(ist(10, 0, 10))
    await rig.recovery.on_disconnected("first")
    rig.clock.set(ist(10, 1, 10))
    await rig.recovery.on_disconnected("second")  # still down: the gap started at the first
    rig.clock.set(ist(10, 2, 30))
    await rig.recovery.on_connected()
    await rig.recovery.wait()
    assert rig.recovery.reports[0].window_start == ist(10, 0)
