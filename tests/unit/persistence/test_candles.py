from datetime import timedelta

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cold import ParquetCandleArchive, ParquetCandleCodec
from emporos.persistence.candle_hot import CandleDocumentMapper
from emporos.persistence.candles import CandleRepository
from tests.support.candles import at, make_candle, minute_series
from tests.support.fakes import InMemoryCandleStore, InMemoryObjectStore

INST = "inst-1"


def _repository() -> tuple[CandleRepository, InMemoryCandleStore, ParquetCandleArchive]:
    hot = InMemoryCandleStore()
    cold = ParquetCandleArchive(InMemoryObjectStore())
    return CandleRepository(hot, cold), hot, cold


def test_parquet_codec_round_trips_every_field_exactly() -> None:
    bars = [
        make_candle(INST, at(2026, 1, 5), "123.45"),
        make_candle(INST, at(2026, 1, 6), partial=True),
    ]

    assert ParquetCandleCodec().decode(ParquetCandleCodec().encode(bars)) == bars


def test_parquet_codec_handles_an_empty_partition() -> None:
    codec = ParquetCandleCodec()

    assert codec.decode(codec.encode([])) == []


def test_document_mapper_round_trips_a_candle() -> None:
    bar = make_candle(INST, at(2026, 1, 5), "99.95", partial=True)
    mapper = CandleDocumentMapper()

    assert mapper.from_document(mapper.to_document(bar)) == bar


async def test_archive_read_filters_to_the_half_open_range_across_months() -> None:
    _, _, cold = _repository()
    bars = [
        make_candle(INST, at(2025, 12, 31, 10)),
        make_candle(INST, at(2026, 1, 1, 4)),
        make_candle(INST, at(2026, 1, 31, 23)),
        make_candle(INST, at(2026, 2, 1, 4)),
    ]
    await cold.archive(bars)

    read = await cold.read(INST, Timeframe.M1, at(2025, 12, 31, 12), at(2026, 2, 1, 4))

    assert read == bars[1:3]


async def test_archiving_is_idempotent_and_merges_with_what_is_already_there() -> None:
    _, _, cold = _repository()
    first = minute_series(INST, at(2026, 1, 5), 3)
    revised = make_candle(INST, first[1].ts, "999.05")
    await cold.archive(first)
    await cold.archive(first)
    await cold.archive([revised, make_candle(INST, at(2026, 1, 5, 4, 10))])

    read = await cold.read(INST, Timeframe.M1, at(2026, 1, 5), at(2026, 1, 6))

    assert [c.ts for c in read] == [first[0].ts, first[1].ts, first[2].ts, at(2026, 1, 5, 4, 10)]
    assert read[1] == revised


async def test_archive_read_of_an_unknown_instrument_is_empty() -> None:
    _, _, cold = _repository()

    assert await cold.read("nope", Timeframe.M1, at(2026, 1, 1), at(2026, 3, 1)) == []


async def test_archive_keeps_timeframes_and_instruments_apart() -> None:
    _, _, cold = _repository()
    await cold.archive([make_candle(INST, at(2026, 1, 5)), make_candle("other", at(2026, 1, 5))])

    read = await cold.read(INST, Timeframe.M5, at(2026, 1, 1), at(2026, 2, 1))

    assert read == []
    assert len(await cold.read("other", Timeframe.M1, at(2026, 1, 1), at(2026, 2, 1))) == 1


@pytest.fixture
def bars() -> list[Candle]:
    return minute_series(INST, at(2026, 1, 30, 4), 6, every=timedelta(days=1))  # Jan 30 .. Feb 4


async def _series(repo: CandleRepository, bars: list[Candle]) -> list[Candle]:
    return await repo.get_range(INST, Timeframe.M1, bars[0].ts, bars[-1].ts + timedelta(seconds=1))


async def test_the_same_series_comes_back_from_hot_only_cold_only_and_spanning(
    bars: list[Candle],
) -> None:
    hot_only, hot, _ = _repository()
    cold_only, _, cold = _repository()
    spanning, span_hot, span_cold = _repository()
    await hot.upsert(bars)
    await cold.archive(bars)
    await span_cold.archive(bars[:3])
    await span_hot.upsert(bars[3:])

    results = [await _series(repo, bars) for repo in (hot_only, cold_only, spanning)]

    assert results == [bars, bars, bars]


async def test_a_bar_present_in_both_tiers_is_returned_once_and_the_hot_copy_wins(
    bars: list[Candle],
) -> None:
    repo, hot, cold = _repository()
    stale = bars[2]
    fresh = make_candle(INST, stale.ts, "777.05")
    await cold.archive([stale])
    await hot.upsert([fresh])

    series = await _series(repo, bars)

    assert [c.ts for c in series].count(stale.ts) == 1
    assert fresh in series and stale not in series


async def test_upsert_writes_to_the_hot_tier_and_is_idempotent(bars: list[Candle]) -> None:
    repo, hot, _ = _repository()

    await repo.upsert(bars)
    await repo.upsert(bars)

    assert await _series(repo, bars) == bars
    assert (
        await hot.read(INST, Timeframe.M1, bars[0].ts, bars[-1].ts + timedelta(seconds=1)) == bars
    )


async def test_an_empty_or_inverted_range_returns_nothing(bars: list[Candle]) -> None:
    repo, hot, _ = _repository()
    await hot.upsert(bars)

    assert await repo.get_range(INST, Timeframe.M1, bars[3].ts, bars[3].ts) == []
    assert await repo.get_range(INST, Timeframe.M1, bars[3].ts, bars[0].ts) == []
