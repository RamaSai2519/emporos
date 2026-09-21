"""EM-58: the rollup moves aged bars from hot to cold with no data loss and no visible change."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candle_rollup import CandleRollup, RollupVerificationError
from emporos.persistence.candles import CandleRepository
from emporos.persistence.placement import RetentionPlacement
from tests.support.fakes import InMemoryCandleStore, InMemoryObjectStore, RecordingAlertSink

NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)

# A long hot tier (the old defaults): these tests exercise WHERE a bar goes, at ages that only make
# sense with these boundaries. The shipped defaults are pinned by one test of their own.
LONG_HOT: dict[Timeframe, int | None] = {
    Timeframe.M1: 90,
    Timeframe.M5: 365,
    Timeframe.M15: 365,
    Timeframe.H1: 365,
    Timeframe.D1: None,
}
START, END = NOW - timedelta(days=800), NOW + timedelta(days=1)


def bar(instrument: str, age_days: float, timeframe: Timeframe = Timeframe.M1) -> Candle:
    p = Money.of(f"{100 + age_days / 100:.2f}")
    ts = (NOW - timedelta(days=age_days)).replace(second=0, microsecond=0)
    return Candle(instrument, timeframe, ts, p, p, p, p, int(age_days * 10))


class Rig:
    def __init__(self) -> None:
        self.hot = InMemoryCandleStore()
        self.cold = ParquetCandleArchive(InMemoryObjectStore())
        self.repo = CandleRepository(self.hot, self.cold)  # reads only: unions both tiers
        self.alerts = RecordingAlertSink()
        self.rollup = CandleRollup(
            self.hot, self.cold, RetentionPlacement(FixedClock(NOW), LONG_HOT), self.alerts
        )

    async def series(self, instrument: str, timeframe: Timeframe = Timeframe.M1) -> list[Candle]:
        return await self.repo.get_range(instrument, timeframe, START, END)

    async def hot_ts(self, instrument: str, timeframe: Timeframe = Timeframe.M1) -> list[datetime]:
        return [b.ts for b in await self.hot.read(instrument, timeframe, START, END)]

    async def seed(self, bars: Iterable[Candle]) -> None:
        await self.hot.upsert(list(bars))  # legacy data: everything written to the hot tier


async def test_aged_bars_move_to_cold_and_reads_are_identical_before_and_after() -> None:
    """Acceptance: read the same range through CandleRepository before and after the rollup."""
    rig = Rig()
    bars = [bar("NSE:1", age) for age in (400, 300, 120, 95, 89, 30, 5)]
    await rig.seed(bars)
    before = await rig.series("NSE:1")

    report = await rig.rollup.run()

    assert report.ok and report.archived == 4 and report.deleted == 4  # the four older than 90d
    assert await rig.series("NSE:1") == before == sorted(bars, key=lambda b: b.ts)
    assert await rig.hot_ts("NSE:1") == sorted(
        b.ts for b in bars if b.ts >= NOW - timedelta(days=90)
    )
    assert len(await rig.cold.read("NSE:1", Timeframe.M1, START, END)) == 4


async def test_every_instrument_is_rolled_up_independently() -> None:
    rig = Rig()
    await rig.seed([bar("NSE:1", 200), bar("NSE:2", 300), bar("NSE:2", 5)])
    before = {i: await rig.series(i) for i in ("NSE:1", "NSE:2")}

    report = await rig.rollup.run()

    assert report.instruments == 2 and report.archived == 2
    assert {i: await rig.series(i) for i in ("NSE:1", "NSE:2")} == before


async def test_each_timeframe_uses_its_own_retention_and_daily_bars_never_move() -> None:
    rig = Rig()
    age = 200  # past 1m's 90d, inside 5m's 365d; daily never leaves
    await rig.seed(
        [bar("NSE:1", age), bar("NSE:1", age, Timeframe.M5), bar("NSE:1", age, Timeframe.D1)]
    )

    await rig.rollup.run()

    assert await rig.hot_ts("NSE:1", Timeframe.M1) == []  # rolled
    assert len(await rig.hot_ts("NSE:1", Timeframe.M5)) == 1  # still hot
    assert len(await rig.hot_ts("NSE:1", Timeframe.D1)) == 1  # never leaves


async def test_a_second_run_finds_nothing_left_to_do() -> None:
    rig = Rig()
    await rig.seed([bar("NSE:1", 200), bar("NSE:1", 5)])
    await rig.rollup.run()
    before = await rig.series("NSE:1")

    again = await rig.rollup.run()

    assert again.archived == again.deleted == 0 and again.ok
    assert await rig.series("NSE:1") == before


async def test_a_crash_between_archive_and_delete_is_finished_by_the_next_run() -> None:
    class DiesOnDelete(InMemoryCandleStore):
        armed = True

        async def delete(
            self, instrument_id: str, timeframe: Timeframe, timestamps: Sequence[datetime]
        ) -> int:
            if self.armed:
                raise ConnectionError("killed mid-rollup")
            return await super().delete(instrument_id, timeframe, timestamps)

    rig = Rig()
    rig.hot = DiesOnDelete()
    rig.repo = CandleRepository(rig.hot, rig.cold)
    rig.rollup = CandleRollup(rig.hot, rig.cold, RetentionPlacement(FixedClock(NOW), LONG_HOT))
    await rig.seed([bar("NSE:1", 200), bar("NSE:1", 5)])
    before = await rig.series("NSE:1")

    with pytest.raises(ConnectionError):
        await rig.rollup.run()
    assert await rig.series("NSE:1") == before  # archived AND still hot: the union shows no change

    rig.hot.armed = False
    report = await rig.rollup.run()

    assert report.ok and report.deleted == 1
    assert await rig.series("NSE:1") == before  # no duplicates, no loss
    assert len(await rig.hot_ts("NSE:1")) == 1


async def test_if_the_archive_does_not_hold_the_bars_nothing_is_deleted() -> None:
    class LossyArchive(ParquetCandleArchive):
        async def archive(self, candles: Iterable[Candle]) -> None:
            await super().archive(list(candles)[:-1])  # silently drops the last bar

    rig = Rig()
    lossy = LossyArchive(InMemoryObjectStore())
    rig.cold = lossy
    rig.repo = CandleRepository(rig.hot, lossy)
    rig.rollup = CandleRollup(
        rig.hot, lossy, RetentionPlacement(FixedClock(NOW), LONG_HOT), rig.alerts
    )
    await rig.seed([bar("NSE:1", 300), bar("NSE:1", 200)])

    report = await rig.rollup.run()

    assert not report.ok and report.deleted == 0
    assert len(await rig.hot_ts("NSE:1")) == 2  # the hot tier is untouched
    assert "RollupVerificationError" in report.failures[0]
    assert [n for n, _ in rig.alerts.alerts] == ["persistence.rollup_failed"]
    assert issubclass(RollupVerificationError, Exception)


async def test_one_instrument_failing_does_not_stop_the_others() -> None:
    class FailsFor(ParquetCandleArchive):
        async def archive(self, candles: Iterable[Candle]) -> None:
            bars = list(candles)
            if bars[0].instrument_id == "NSE:1":
                raise ConnectionError("s3 down")
            await super().archive(bars)

    rig = Rig()
    flaky = FailsFor(InMemoryObjectStore())
    rig.repo = CandleRepository(rig.hot, flaky)
    rig.rollup = CandleRollup(rig.hot, flaky, RetentionPlacement(FixedClock(NOW), LONG_HOT))
    await rig.seed([bar("NSE:1", 200), bar("NSE:2", 200)])

    report = await rig.rollup.run()

    assert len(report.failures) == 1 and "NSE:1" in report.failures[0]
    assert await rig.hot_ts("NSE:2") == [] and len(await rig.hot_ts("NSE:1")) == 1
    assert len(await rig.series("NSE:1")) == 1 and len(await rig.series("NSE:2")) == 1


async def test_only_the_bars_that_were_archived_are_deleted() -> None:
    """A bar that appears in the hot tier after the read must survive (delete is by timestamp)."""

    class LateWriter(InMemoryCandleStore):
        async def read(self, instrument_id, timeframe, start, end):  # type: ignore[no-untyped-def]
            bars = await super().read(instrument_id, timeframe, start, end)
            await self.upsert([bar("NSE:1", 150)])  # arrives after the rollup's read
            return bars

    rig = Rig()
    rig.hot = LateWriter()
    rig.repo = CandleRepository(rig.hot, rig.cold)
    rig.rollup = CandleRollup(rig.hot, rig.cold, RetentionPlacement(FixedClock(NOW), LONG_HOT))
    await rig.seed([bar("NSE:1", 200)])

    await rig.rollup.run()

    assert bar("NSE:1", 150).ts in await rig.hot_ts("NSE:1")  # not swept up with the archived bar


async def test_a_run_can_be_limited_to_some_timeframes() -> None:
    rig = Rig()
    await rig.seed([bar("NSE:1", 200), bar("NSE:1", 400, Timeframe.M5)])

    await rig.rollup.run([Timeframe.M5])

    assert len(await rig.hot_ts("NSE:1", Timeframe.M1)) == 1  # untouched
    assert await rig.hot_ts("NSE:1", Timeframe.M5) == []


async def test_a_run_can_be_limited_to_some_instruments() -> None:
    rig = Rig()
    await rig.seed([bar("NSE:1", 200), bar("NSE:2", 200)])

    report = await rig.rollup.run(instrument_ids=["NSE:2"])

    assert report.instruments == 1
    assert len(await rig.hot_ts("NSE:1")) == 1 and await rig.hot_ts("NSE:2") == []
