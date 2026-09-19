"""EM-56: gaps are found in STORED history, filled from the broker alone, and holidays are never
gaps; the broker's own silent minutes are learned once and never chased again."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from emporos.core.clock import IST, FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.history.backfill import BackfillOrchestrator
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.gaps import GapDetector, GapFiller, HistoryReconciler
from emporos.history.grid import SessionGrid
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candles import CandleRepository
from tests.support.fakes import (
    InMemoryCandleStore,
    InMemoryCoverageStore,
    InMemoryObjectStore,
    make_instrument,
)
from tests.support.history import BrokerHistory, bar, session_minutes

SBIN = make_instrument("3045")
DAY = date(2026, 9, 16)
NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
HOLIDAY = date(2026, 9, 14)


def ist_minutes(day: date, hour: int, minute: int, count: int) -> list[datetime]:
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).astimezone(UTC)
    return [start + timedelta(minutes=i) for i in range(count)]


class Rig:
    def __init__(self, history: BrokerHistory | None = None) -> None:
        self.history = history or BrokerHistory()
        self.store = InMemoryCandleStore()
        self.repo = CandleRepository(self.store, ParquetCandleArchive(InMemoryObjectStore()))
        self.coverage = InMemoryCoverageStore()
        self.calendar = StoredTradingCalendar({HOLIDAY: False})
        self.grid = SessionGrid(self.calendar)
        clock = FixedClock(NOW)
        self.backfill = BackfillOrchestrator(
            self.history, self.repo, self.coverage, self.grid, clock
        )
        self.detector = GapDetector(self.repo, self.coverage, self.grid)
        self.filler = GapFiller(self.history, self.repo, self.repo, self.coverage, self.grid, clock)

    async def m1(self) -> list[Candle]:
        return await self.repo.get_range(
            SBIN.instrument_id, Timeframe.M1, datetime(2026, 1, 1, tzinfo=UTC), NOW
        )


async def test_complete_history_has_no_gaps_even_when_the_broker_omits_minutes() -> None:
    silent_tail = lambda ts: ts.astimezone(IST).hour == 15 and ts.minute >= 15  # noqa: E731
    rig = Rig(BrokerHistory(silent=silent_tail))
    await rig.backfill.run([SBIN], date(2026, 9, 15), date(2026, 9, 17))

    assert await rig.detector.find(SBIN.instrument_id, date(2026, 9, 15), date(2026, 9, 17)) == []


async def test_a_deliberately_introduced_gap_is_detected_exactly() -> None:
    rig = Rig()
    await rig.backfill.run([SBIN], DAY, DAY)
    lost = ist_minutes(DAY, 11, 30, 10)
    rig.store.remove(SBIN.instrument_id, Timeframe.M1, lost)

    gaps = await rig.detector.find(SBIN.instrument_id, DAY, DAY)

    assert [(g.range.start, g.range.end) for g in gaps] == [
        (lost[0], lost[-1] + timedelta(minutes=1))
    ]
    assert gaps[0].day == DAY


async def test_a_detected_gap_is_refetched_alone_and_then_the_history_is_whole() -> None:
    rig = Rig()
    await rig.backfill.run([SBIN], DAY, DAY)
    lost = ist_minutes(DAY, 11, 30, 10)
    rig.store.remove(SBIN.instrument_id, Timeframe.M1, lost)
    requests_before = len(rig.history.requests)
    gaps = await rig.detector.find(SBIN.instrument_id, DAY, DAY)

    report = await rig.filler.fill(SBIN, gaps)

    assert report.ok and report.candles_written == 10
    (_, start, end) = rig.history.requests[requests_before:][0]
    assert (start, end) == (lost[0], lost[-1] + timedelta(minutes=1))  # only the gap, not the day
    assert len(rig.history.requests) == requests_before + 1
    assert await rig.detector.find(SBIN.instrument_id, DAY, DAY) == []
    assert len(await rig.m1()) == 375


async def test_a_gap_fill_never_overwrites_bars_that_already_exist() -> None:
    rig = Rig()
    await rig.backfill.run([SBIN], DAY, DAY)
    lost = ist_minutes(DAY, 11, 30, 3)
    rig.store.remove(SBIN.instrument_id, Timeframe.M1, [lost[0], lost[2]])  # two holes...
    marker = bar(SBIN, lost[1], "555.55", volume=999)  # ...with OUR bar between them
    await rig.store.upsert([marker])
    (first, second) = await rig.detector.find(SBIN.instrument_id, DAY, DAY)
    assert first.range.start == lost[0] and second.range.start == lost[2]

    await rig.filler.fill(SBIN, [first, second])  # one fetch spans the marker minute

    assert [b for b in await rig.m1() if b.ts == lost[1]] == [marker]  # untouched
    assert {b.ts for b in await rig.m1()} >= {lost[0], lost[2]}  # the holes were filled


async def test_higher_timeframes_are_rebuilt_after_a_fill() -> None:
    rig = Rig()
    await rig.backfill.run([SBIN], DAY, DAY)
    lost = ist_minutes(DAY, 11, 30, 5)
    rig.store.remove(SBIN.instrument_id, Timeframe.M1, lost)
    rig.store.remove(SBIN.instrument_id, Timeframe.M5, [lost[0]])  # the derived bar was lost too

    await rig.filler.fill(SBIN, await rig.detector.find(SBIN.instrument_id, DAY, DAY))

    assert lost[0] in {ts for (_, tf, ts) in rig.store.bars() if tf is Timeframe.M5}


async def test_minutes_the_broker_cannot_supply_are_learned_and_not_chased_again() -> None:
    always_silent = set(ist_minutes(DAY, 12, 0, 4))
    rig = Rig(BrokerHistory(silent=lambda ts: ts in always_silent))
    # history that was never backfilled: the whole day is a gap...
    (gap,) = await rig.detector.find(SBIN.instrument_id, DAY, DAY)
    assert (gap.range.start, gap.range.end) == (
        session_minutes(DAY)[0],
        session_minutes(DAY)[-1] + timedelta(minutes=1),
    )

    report = await rig.filler.fill(SBIN, [gap])
    requests = len(rig.history.requests)

    assert report.candles_written == 375 - 4
    assert (
        await rig.detector.find(SBIN.instrument_id, DAY, DAY) == []
    )  # the 4 silent minutes are known
    await rig.filler.fill(SBIN, await rig.detector.find(SBIN.instrument_id, DAY, DAY))
    assert len(rig.history.requests) == requests  # and nothing is fetched a second time


async def test_holidays_weekends_and_days_beyond_the_calendar_are_never_gaps() -> None:
    rig = Rig()
    gaps = await rig.detector.find(SBIN.instrument_id, date(2026, 9, 12), date(2026, 9, 14))
    assert gaps == []  # Sat, Sun, and the calendar's Monday holiday: empty history, no gaps

    weekend_only = await rig.detector.find(SBIN.instrument_id, date(2026, 9, 19), date(2026, 9, 20))
    assert weekend_only == []


async def test_an_untouched_trading_day_is_one_whole_day_gap() -> None:
    rig = Rig()
    gaps = await rig.detector.find(SBIN.instrument_id, date(2026, 9, 15), date(2026, 9, 16))
    assert [g.day for g in gaps] == [date(2026, 9, 15), date(2026, 9, 16)]


async def test_gaps_on_several_days_are_fetched_one_request_per_day() -> None:
    rig = Rig()
    await rig.backfill.run([SBIN], date(2026, 9, 15), date(2026, 9, 17))
    for day, hour in ((date(2026, 9, 15), 10), (date(2026, 9, 17), 13)):
        rig.store.remove(SBIN.instrument_id, Timeframe.M1, ist_minutes(day, hour, 0, 6))
        rig.store.remove(SBIN.instrument_id, Timeframe.M1, ist_minutes(day, hour + 1, 0, 6))
    before = len(rig.history.requests)

    report = await rig.filler.fill(
        SBIN, await rig.detector.find(SBIN.instrument_id, date(2026, 9, 15), date(2026, 9, 17))
    )

    assert report.gaps_found == 4 and report.gaps_filled == 4
    assert len(rig.history.requests) - before == 2  # two days, not four gaps


async def test_a_failed_refetch_keeps_the_gap_open_for_the_next_attempt() -> None:
    rig = Rig()
    await rig.backfill.run([SBIN], DAY, DAY)
    rig.store.remove(SBIN.instrument_id, Timeframe.M1, ist_minutes(DAY, 11, 30, 10))
    gaps = await rig.detector.find(SBIN.instrument_id, DAY, DAY)
    rig.history.fail_requests = {len(rig.history.requests) + 1}

    report = await rig.filler.fill(SBIN, gaps)

    assert not report.ok and report.failed_days == [(SBIN.instrument_id, DAY)]
    assert await rig.detector.find(SBIN.instrument_id, DAY, DAY) == gaps  # still a gap
    assert (await rig.filler.fill(SBIN, gaps)).ok  # and the retry succeeds


async def test_the_reconciler_fixes_every_instrument_in_one_pass() -> None:
    rig = Rig()
    other = make_instrument("2885")
    await rig.backfill.run([SBIN, other], DAY, DAY)
    rig.store.remove(SBIN.instrument_id, Timeframe.M1, ist_minutes(DAY, 10, 0, 5))
    rig.store.remove(other.instrument_id, Timeframe.M1, ist_minutes(DAY, 14, 0, 5))

    reports = await HistoryReconciler(rig.detector, rig.filler).reconcile([SBIN, other], DAY, DAY)

    assert {i: r.candles_written for i, r in reports.items()} == {
        SBIN.instrument_id: 5,
        other.instrument_id: 5,
    }
