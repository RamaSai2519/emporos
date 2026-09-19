"""EM-55: resumable, chunked, idempotent backfill; survives failures; never marks unwritten days."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime

import pytest

from emporos.core.clock import IST, FixedClock
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.history.backfill import BackfillOrchestrator
from emporos.history.grid import SessionGrid
from emporos.marketdata.session import WeekdayCalendar
from tests.support.fakes import (
    InMemoryCandleStore,
    InMemoryCoverageStore,
    RecordingAlertSink,
    make_instrument,
)
from tests.support.history import BrokerHistory

SBIN, RELIANCE = make_instrument("3045"), make_instrument("2885")
FIRST, LAST = date(2026, 8, 3), date(2026, 9, 18)  # Mon .. Fri, ~6.5 weeks
NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
DAYS = 35  # weekdays in FIRST..LAST


class Rig:
    def __init__(self, history: BrokerHistory | None = None, chunk_days: int = 28) -> None:
        self.history = history or BrokerHistory()
        self.store = InMemoryCandleStore()
        self.coverage = InMemoryCoverageStore()
        self.alerts = RecordingAlertSink()
        self.orchestrator = BackfillOrchestrator(
            self.history,
            self.store,
            self.coverage,
            SessionGrid(WeekdayCalendar()),
            FixedClock(NOW),
            self.alerts,
            chunk_days=chunk_days,
        )

    async def m1(self, instrument=SBIN) -> list[Candle]:  # type: ignore[no-untyped-def]
        return await self.store.read(
            instrument.instrument_id, Timeframe.M1, datetime(2026, 1, 1, tzinfo=UTC), NOW
        )


async def test_a_long_window_is_fetched_in_chunks_within_the_brokers_limit() -> None:
    rig = Rig()

    report = await rig.orchestrator.run([SBIN], FIRST, LAST)

    spans = [(end - start).days for _, start, end in rig.history.requests]
    assert report.ok and report.chunks_fetched == len(rig.history.requests) == 2
    assert all(days <= 30 for days in spans)  # never beyond what the broker will not truncate
    assert len(await rig.m1()) == DAYS * 375  # 32 weekdays, every minute present


async def test_the_chunk_limit_is_validated_against_the_brokers_truncation_threshold() -> None:
    with pytest.raises(ConfigurationError):
        Rig(chunk_days=31)
    with pytest.raises(ConfigurationError):
        Rig(chunk_days=0)


async def test_a_rerun_after_completion_fetches_nothing_and_changes_nothing() -> None:
    rig = Rig()
    await rig.orchestrator.run([SBIN], FIRST, LAST)
    before = await rig.m1()
    requests = len(rig.history.requests)

    report = await rig.orchestrator.run([SBIN], FIRST, LAST)

    assert len(rig.history.requests) == requests  # no duplicate work
    assert report.chunks_fetched == 0 and report.days_already_covered == DAYS
    assert await rig.m1() == before  # no duplicates, no drift


async def test_killing_the_run_mid_way_resumes_from_the_last_checkpoint() -> None:
    """Acceptance: interrupt mid-backfill, restart, and no completed work is repeated."""

    class Killed(BaseException):
        pass

    class KillAfterFirstChunk(BrokerHistory):
        async def fetch(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if len(self.requests) == 1:
                raise Killed  # the process dies while the second chunk is in flight
            return await super().fetch(*args, **kwargs)

    rig = Rig(KillAfterFirstChunk())
    with pytest.raises(Killed):
        await rig.orchestrator.run([SBIN], FIRST, LAST)
    first_chunk_days = len({b.ts.astimezone(IST).date() for b in await rig.m1()})
    assert 0 < first_chunk_days < DAYS  # a checkpoint exists for the finished chunk only

    resumed = Rig(BrokerHistory())
    resumed.store, resumed.coverage = rig.store, rig.coverage  # same durable state after restart
    resumed.orchestrator = BackfillOrchestrator(
        resumed.history, rig.store, rig.coverage, SessionGrid(WeekdayCalendar()), FixedClock(NOW)
    )
    report = await resumed.orchestrator.run([SBIN], FIRST, LAST)

    assert report.days_already_covered == first_chunk_days  # finished days were skipped...
    ((_, start, _),) = resumed.history.requests  # ...and only the remaining chunk was fetched
    assert start.astimezone(IST).date() > FIRST
    assert len(await resumed.m1()) == DAYS * 375  # complete, and nothing doubled


async def test_cancellation_propagates_and_marks_nothing_covered() -> None:
    class Hangs(BrokerHistory):
        async def fetch(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            await asyncio.sleep(3600)
            return []

    rig = Rig(Hangs())
    task = asyncio.create_task(rig.orchestrator.run([SBIN], FIRST, LAST))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await rig.coverage.get_days(SBIN.instrument_id, Timeframe.M1, FIRST, LAST) == {}


async def test_a_spurious_rate_limit_failure_leaves_the_chunk_uncovered_and_it_is_retried() -> None:
    history = BrokerHistory()
    history.fail_requests = {1}  # the defect denies the first request outright
    rig = Rig(history)

    report = await rig.orchestrator.run([SBIN], FIRST, LAST)

    assert not report.ok and len(report.failed_chunks) == 1 and report.chunks_fetched == 1
    assert [n for n, _ in rig.alerts.alerts] == ["history.backfill_incomplete"]

    retry = await rig.orchestrator.run(
        [SBIN], FIRST, LAST
    )  # the next run picks up only the failure

    assert retry.ok and retry.chunks_fetched == 1
    assert len(await rig.m1()) == DAYS * 375


async def test_one_instrument_failing_does_not_stop_the_others() -> None:
    history = BrokerHistory()
    history.fail_requests = {1, 2}  # both chunks of the first instrument
    rig = Rig(history)

    report = await rig.orchestrator.run([SBIN, RELIANCE], FIRST, LAST)

    assert {f[0] for f in report.failed_chunks} == {SBIN.instrument_id}
    assert len(await rig.m1(RELIANCE)) == DAYS * 375


async def test_minutes_the_broker_omits_are_recorded_as_confirmed_absent() -> None:
    rig = Rig(
        BrokerHistory(silent=lambda ts: ts.astimezone(IST).time().hour == 15 and ts.minute % 2 == 0)
    )

    await rig.orchestrator.run([SBIN], date(2026, 9, 14), date(2026, 9, 14))

    (coverage,) = (
        await rig.coverage.get_days(
            SBIN.instrument_id, Timeframe.M1, date(2026, 9, 14), date(2026, 9, 14)
        )
    ).values()
    assert coverage.complete and not coverage.empty
    assert len(await rig.m1()) == 375 - 15  # 15:00..15:29 has 15 even minutes
    assert sum(int((r.end - r.start).total_seconds() // 60) for r in coverage.absent) == 15


async def test_holidays_and_weekends_are_never_requested() -> None:
    rig = Rig()
    await rig.orchestrator.run([SBIN], date(2026, 9, 19), date(2026, 9, 20))  # Sat, Sun
    assert rig.history.requests == []


async def test_an_empty_trading_day_is_flagged_not_silently_accepted() -> None:
    rig = Rig(BrokerHistory(silent=lambda ts: ts.astimezone(IST).date() == date(2026, 9, 15)))

    report = await rig.orchestrator.run([SBIN], date(2026, 9, 14), date(2026, 9, 16))

    assert report.empty_days == [(SBIN.instrument_id, date(2026, 9, 15))]
    assert "history.empty_trading_day" in [n for n, _ in rig.alerts.alerts]
    days = await rig.coverage.get_days(
        SBIN.instrument_id, Timeframe.M1, date(2026, 9, 14), date(2026, 9, 16)
    )
    assert days[date(2026, 9, 15)].empty and not days[date(2026, 9, 14)].empty


async def test_higher_timeframes_are_derived_and_written_alongside_the_minutes() -> None:
    rig = Rig()
    await rig.orchestrator.run([SBIN], date(2026, 9, 14), date(2026, 9, 14))

    async def count(tf: Timeframe) -> int:
        return len(
            await rig.store.read(SBIN.instrument_id, tf, datetime(2026, 1, 1, tzinfo=UTC), NOW)
        )

    assert (await count(Timeframe.M5), await count(Timeframe.M15), await count(Timeframe.H1)) == (
        75,
        25,
        7,
    )


async def test_bars_outside_the_session_window_are_ignored() -> None:
    class Noisy(BrokerHistory):
        async def fetch(self, instrument, timeframe, start, end):  # type: ignore[no-untyped-def]
            bars = await super().fetch(instrument, timeframe, start, end)
            from tests.support.history import bar

            return [*bars, bar(instrument, start.replace(hour=1))]  # 06:xx IST: pre-session junk

    rig = Rig(Noisy())
    await rig.orchestrator.run([SBIN], date(2026, 9, 14), date(2026, 9, 14))
    assert len(await rig.m1()) == 375


async def test_a_failed_write_never_leaves_a_day_marked_covered() -> None:
    """The ordering guarantee behind safe resume: coverage is recorded only AFTER the bars are."""

    class BrokenStore(InMemoryCandleStore):
        async def upsert(self, candles: Sequence[Candle]) -> None:
            raise ConnectionError("mongo down")

    rig = Rig()
    rig.orchestrator = BackfillOrchestrator(
        rig.history, BrokenStore(), rig.coverage, SessionGrid(WeekdayCalendar()), FixedClock(NOW)
    )

    with pytest.raises(ConnectionError):
        await rig.orchestrator.run([SBIN], FIRST, LAST)

    assert await rig.coverage.get_days(SBIN.instrument_id, Timeframe.M1, FIRST, LAST) == {}
