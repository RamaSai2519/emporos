"""EM-52: candles reach storage only through CandleWriter, idempotently, and are never lost."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from emporos.domain.candles import Candle, Timeframe
from emporos.marketdata.candle_writer import CandlePersister
from tests.support.candles import make_candle
from tests.support.fakes import InMemoryCandleStore, RecordingAlertSink

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)


class FlakyWriter:
    """`CandleWriter` double that can be told to fail."""

    def __init__(self) -> None:
        self.batches: list[list[Candle]] = []
        self.fail = False

    async def upsert(self, candles: Sequence[Candle]) -> None:
        if self.fail:
            raise ConnectionError("mongo unavailable")
        self.batches.append(list(candles))


def bars(count: int) -> list[Candle]:
    return [make_candle("NSE:1", T0.replace(minute=30 + n)) for n in range(count)]


async def test_candles_are_buffered_then_written_in_bounded_batches_in_order() -> None:
    writer = FlakyWriter()
    persister = CandlePersister(writer, batch_size=2)
    for candle in bars(5):
        persister.on_candle(candle)
    assert persister.pending == 5 and writer.batches == []

    written = await persister.flush()

    assert written == 5 and persister.pending == 0
    assert [len(b) for b in writer.batches] == [2, 2, 1]
    assert [c.ts.minute for b in writer.batches for c in b] == [30, 31, 32, 33, 34]


async def test_a_failed_write_keeps_every_candle_alerts_and_the_retry_succeeds() -> None:
    writer, alerts = FlakyWriter(), RecordingAlertSink()
    persister = CandlePersister(writer, alerts)
    for candle in bars(3):
        persister.on_candle(candle)
    writer.fail = True

    assert await persister.flush() == 0  # never raises

    assert persister.pending == 3 and persister.failures == 1
    assert [name for name, _ in alerts.alerts] == ["market_data.candle_write_failed"]
    writer.fail = False
    assert await persister.flush() == 3 and persister.pending == 0


async def test_a_partial_failure_keeps_only_the_unwritten_tail() -> None:
    class FailsOnSecondBatch(FlakyWriter):
        async def upsert(self, candles: Sequence[Candle]) -> None:
            if self.batches:
                raise ConnectionError("dropped mid-flush")
            await super().upsert(candles)

    persister = CandlePersister(FailsOnSecondBatch(), batch_size=2)
    for candle in bars(5):
        persister.on_candle(candle)

    assert await persister.flush() == 2
    assert persister.pending == 3


async def test_writing_the_same_candles_twice_creates_no_duplicates() -> None:
    """Idempotent upsert: re-running aggregation over the same ticks cannot duplicate rows."""
    store = InMemoryCandleStore()
    first = CandlePersister(store)
    for candle in bars(4):
        first.on_candle(candle)
    await first.flush()

    rerun = CandlePersister(store)  # a restarted process replaying the same session
    for candle in bars(4):
        rerun.on_candle(candle)
    await rerun.flush()

    stored = await store.read("NSE:1", Timeframe.M1, T0.replace(minute=0), T0.replace(minute=59))
    assert [c.ts.minute for c in stored] == [30, 31, 32, 33]


async def test_flushing_with_nothing_pending_is_a_no_op() -> None:
    writer = FlakyWriter()
    assert await CandlePersister(writer).flush() == 0 and writer.batches == []
