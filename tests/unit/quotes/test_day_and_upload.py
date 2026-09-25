"""EM-236: one recording day (wait, poll, close, holiday, weekend, stop) and the day's upload."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emporos.broker.models import Quote
from emporos.core.clock import IST, FixedClock
from emporos.domain.money import Money
from emporos.quotes.day import DayOutcome, QuoteRecordingDay
from emporos.quotes.recorder import QuoteRecorder, RecorderSettings
from emporos.quotes.row import QuoteRow
from emporos.quotes.upload import DayUploader
from emporos.quotes.window import RecordingWindow
from tests.support.fakes import AdvancingSleeper

FRIDAY = datetime(2026, 9, 25, tzinfo=IST)
IDS = ["NSE:1", "NSE:2"]


class Source:
    def __init__(self, clock: FixedClock, stale: bool = False) -> None:
        self.clock, self.stale, self.calls = clock, stale, 0

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        self.calls += 1
        at = (self.clock.now() - timedelta(days=1 if self.stale else 0)).astimezone(UTC)
        return [
            Quote(i, Money.of("10"), Money.of("9"), Money.of("11"), Money.of("8"), Money.of("9"),
                  5, at, bid=Money.of("9.95"), ask=Money.of("10.05"), bid_qty=1, ask_qty=2)
            for i in instrument_ids
        ]  # fmt: skip


class Sink:
    def __init__(self) -> None:
        self.rows: list[QuoteRow] = []
        self.flushes = 0
        self._pending = 0

    def append(self, rows: Sequence[QuoteRow]) -> None:
        self.rows.extend(rows)
        self._pending += len(rows)

    def flush(self) -> int:
        n, self._pending = self._pending, 0
        self.flushes += 1 if n else 0
        return n


def rig(at: datetime, stale: bool = False) -> tuple[QuoteRecordingDay, Source, Sink, FixedClock]:
    clock = FixedClock(at)
    source, sink = Source(clock, stale), Sink()
    window = RecordingWindow()
    recorder = QuoteRecorder(source, sink, IDS, RecorderSettings(), clock, window)
    day = QuoteRecordingDay(recorder, clock, AdvancingSleeper(clock), window)
    return day, source, sink, clock


class TestTheDay:
    async def test_it_waits_for_the_open_polls_each_minute_and_writes_at_the_close(self) -> None:
        day, source, sink, clock = rig(FRIDAY.replace(hour=8, minute=41))

        outcome = await day.run()

        assert outcome is DayOutcome.RECORDED
        assert source.calls == 375  # 09:15 to 15:29, one a minute
        assert len(sink.rows) == 375 * len(IDS)
        assert clock.now().astimezone(IST).time().hour == 15  # it stopped at the close
        assert sink.flushes >= 1  # and nothing stays in memory

    async def test_an_exchange_holiday_writes_nothing_and_ends_cleanly(self) -> None:
        day, source, sink, _ = rig(FRIDAY.replace(hour=9, minute=10), stale=True)

        outcome = await day.run()

        assert outcome is DayOutcome.HOLIDAY
        assert sink.rows == [] and source.calls == 3  # decided after three empty polls

    async def test_a_weekend_never_calls_the_broker(self) -> None:
        day, source, sink, _ = rig(datetime(2026, 9, 26, 8, 41, tzinfo=IST))

        assert await day.run() is DayOutcome.NOT_A_WEEKDAY
        assert source.calls == 0 and sink.rows == []

    async def test_started_after_the_close_does_nothing(self) -> None:
        day, source, _, _ = rig(FRIDAY.replace(hour=16, minute=5))

        assert await day.run() is DayOutcome.AFTER_CLOSE
        assert source.calls == 0

    async def test_a_stop_signal_writes_what_is_in_memory_and_propagates(self) -> None:
        day, source, sink, clock = rig(FRIDAY.replace(hour=10))
        task = asyncio.ensure_future(day.run())
        while source.calls < 3:
            await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert sink.flushes >= 1 and len(sink.rows) >= 3 * len(IDS)
        assert sink._pending == 0  # every row was handed to the sink's flush

    def test_a_holiday_needs_at_least_one_poll(self) -> None:
        clock = FixedClock(FRIDAY)
        recorder = QuoteRecorder(Source(clock), Sink(), IDS, RecorderSettings(), clock)
        with pytest.raises(ValueError, match="at least one"):
            QuoteRecordingDay(
                recorder, clock, AdvancingSleeper(clock), RecordingWindow(), holiday_after_polls=0
            )


class Stat:
    def __init__(self, size: int) -> None:
        self.size = size


class Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail: set[str] = set()

    async def put(self, key: str, data: bytes) -> None:
        if any(key.endswith(name) for name in self.fail):
            raise OSError("denied")
        self.objects[key] = data

    async def stat(self, key: str) -> Stat | None:
        data = self.objects.get(key)
        return None if data is None else Stat(len(data))


def files(root: Path, day: str, **parts: bytes) -> None:
    directory = root / f"date={day}"
    directory.mkdir(parents=True)
    for name, data in parts.items():
        (directory / f"part-{name}.parquet").write_bytes(data)
    (directory / "part-x.tmp").write_bytes(b"half written")  # never uploaded


class TestUpload:
    async def test_every_part_file_goes_under_the_day_and_a_repeat_sends_nothing(
        self, tmp_path: Path
    ) -> None:
        files(tmp_path, "2026-09-25", a=b"1", b=b"22")
        store = Store()
        uploader = DayUploader(store, tmp_path)

        first = await uploader.upload(FRIDAY.date())
        again = await uploader.upload(FRIDAY.date())

        assert sorted(store.objects) == [
            "quotes/date=2026-09-25/part-a.parquet",
            "quotes/date=2026-09-25/part-b.parquet",
        ]
        assert (first.uploaded, first.skipped) == (2, 0)
        assert (again.uploaded, again.skipped) == (0, 2)

    async def test_a_new_part_after_a_restart_is_the_only_one_sent(self, tmp_path: Path) -> None:
        files(tmp_path, "2026-09-25", a=b"1")
        store = Store()
        uploader = DayUploader(store, tmp_path)
        await uploader.upload(FRIDAY.date())
        (tmp_path / "date=2026-09-25" / "part-c.parquet").write_bytes(b"333")

        report = await uploader.upload(FRIDAY.date())

        assert (report.uploaded, report.skipped) == (1, 1)

    async def test_one_failing_file_is_counted_and_the_rest_still_go(self, tmp_path: Path) -> None:
        files(tmp_path, "2026-09-25", a=b"1", b=b"2")
        store = Store()
        store.fail.add("part-a.parquet")

        report = await DayUploader(store, tmp_path).upload(FRIDAY.date())

        assert (report.uploaded, report.failed) == (1, 1)

    async def test_a_day_with_no_files_uploads_nothing(self, tmp_path: Path) -> None:
        report = await DayUploader(Store(), tmp_path).upload(FRIDAY.date())

        assert (report.uploaded, report.skipped, report.failed) == (0, 0, 0)
