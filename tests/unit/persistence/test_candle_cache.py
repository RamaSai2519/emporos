"""The candle cache: a closed month is fetched once and then served from a file, an open or empty
month is never remembered, and what it serves is exactly what the source held."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.candle_cache import CachingCandleReader, CandleCacheFiles

INSTRUMENT = "NSE:1333"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def candle(ts: datetime, price: str = "100.5", instrument: str = INSTRUMENT) -> Candle:
    close = Decimal(price)
    return Candle(
        instrument, Timeframe.M5, ts, Money(close), Money(close + 1), Money(close - 1),
        Money(close), 1200,
    )  # fmt: skip


class CountingSource:
    """A CandleReader over a fixed list that remembers every request it was asked."""

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles
        self.requests: list[tuple[str, datetime, datetime]] = []

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.requests.append((instrument_id, start, end))
        return [
            c for c in self._candles
            if c.instrument_id == instrument_id and start <= c.ts < end
        ]  # fmt: skip


def march_bars() -> list[Candle]:
    first = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    return [candle(first + timedelta(minutes=5 * n), str(100 + n)) for n in range(6)]


def reader(source: CountingSource, root: Path, now: datetime = NOW) -> CachingCandleReader:
    return CachingCandleReader(source, CandleCacheFiles(root), FixedClock(now))


MARCH = (datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC))


async def test_a_closed_month_is_fetched_once_and_then_served_from_the_file(tmp_path: Path) -> None:
    source = CountingSource(march_bars())
    cache = reader(source, tmp_path)

    first = await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH)
    second = await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH)

    assert first == second == march_bars()
    assert len(source.requests) == 1
    assert list(tmp_path.rglob("2026-03.parquet"))


async def test_a_new_reader_finds_the_file_and_never_asks_the_source(tmp_path: Path) -> None:
    await reader(CountingSource(march_bars()), tmp_path).get_range(INSTRUMENT, Timeframe.M5, *MARCH)
    silent = CountingSource([])

    served = await reader(silent, tmp_path).get_range(INSTRUMENT, Timeframe.M5, *MARCH)

    assert served == march_bars() and silent.requests == []


async def test_prices_come_back_exact(tmp_path: Path) -> None:
    exact = candle(datetime(2026, 3, 2, 4, 0, tzinfo=UTC), "1234.565")
    await reader(CountingSource([exact]), tmp_path).get_range(INSTRUMENT, Timeframe.M5, *MARCH)

    (back,) = await reader(CountingSource([]), tmp_path).get_range(INSTRUMENT, Timeframe.M5, *MARCH)

    assert back == exact and back.close == Money.of("1234.565")


async def test_a_range_inside_the_month_is_sliced_and_a_range_across_months_is_joined(
    tmp_path: Path,
) -> None:
    march = march_bars()
    april = [candle(datetime(2026, 4, 1, 3, 45, tzinfo=UTC), "200")]
    cache = reader(CountingSource([*march, *april]), tmp_path)

    inside = await cache.get_range(
        INSTRUMENT,
        Timeframe.M5,
        march[2].ts,
        march[4].ts,  # end is exclusive
    )
    across = await cache.get_range(
        INSTRUMENT, Timeframe.M5, march[4].ts, datetime(2026, 4, 2, tzinfo=UTC)
    )

    assert inside == march[2:4]
    assert across == [march[4], march[5], april[0]]


async def test_an_open_month_is_never_cached(tmp_path: Path) -> None:
    this_month = candle(datetime(2026, 9, 18, 4, 0, tzinfo=UTC))
    source = CountingSource([this_month])
    cache = reader(source, tmp_path)
    september = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC))

    await cache.get_range(INSTRUMENT, Timeframe.M5, *september)
    await cache.get_range(INSTRUMENT, Timeframe.M5, *september)

    assert len(source.requests) == 2 and not list(tmp_path.rglob("*.parquet"))


async def test_a_month_that_ended_only_yesterday_is_not_final_yet(tmp_path: Path) -> None:
    august = (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))
    source = CountingSource([candle(datetime(2026, 8, 31, 4, 0, tzinfo=UTC))])
    cache = reader(source, tmp_path, now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC))

    await cache.get_range(INSTRUMENT, Timeframe.M5, *august)
    await cache.get_range(INSTRUMENT, Timeframe.M5, *august)

    assert len(source.requests) == 2  # ended 1 day and 12 hours ago: bars may still be corrected


async def test_an_empty_month_is_not_remembered_so_a_later_backfill_shows_up(
    tmp_path: Path,
) -> None:
    bars: list[Candle] = []
    source = CountingSource(bars)
    cache = reader(source, tmp_path)
    assert await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH) == []

    bars.extend(march_bars())  # the history fetch fills the gap

    assert await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH) == march_bars()


async def test_instruments_and_timeframes_do_not_share_files(tmp_path: Path) -> None:
    other = [candle(c.ts, "7", "NSE:2885") for c in march_bars()]
    cache = reader(CountingSource([*march_bars(), *other]), tmp_path)

    mine = await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH)
    theirs = await cache.get_range("NSE:2885", Timeframe.M5, *MARCH)

    assert mine == march_bars() and theirs == other
    assert len(list(tmp_path.rglob("2026-03.parquet"))) == 2


async def test_an_empty_or_backwards_range_asks_nobody(tmp_path: Path) -> None:
    source = CountingSource(march_bars())
    cache = reader(source, tmp_path)

    assert await cache.get_range(INSTRUMENT, Timeframe.M5, MARCH[1], MARCH[0]) == []
    assert await cache.get_range(INSTRUMENT, Timeframe.M5, MARCH[0], MARCH[0]) == []
    assert source.requests == []


async def test_clear_removes_the_files_and_the_next_read_asks_the_source_again(
    tmp_path: Path,
) -> None:
    source = CountingSource(march_bars())
    cache = reader(source, tmp_path)
    await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH)

    removed = cache.clear()
    await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH)

    assert removed == 1 and len(source.requests) == 2


async def test_the_fingerprint_names_the_files_and_changes_when_they_do(tmp_path: Path) -> None:
    cache = reader(CountingSource(march_bars()), tmp_path)
    empty = cache.fingerprint()
    await cache.get_range(INSTRUMENT, Timeframe.M5, *MARCH)

    filled = cache.fingerprint()

    assert empty != filled and filled.startswith("candle-cache:")
    assert cache.fingerprint() == filled  # stable while nothing changes


async def test_december_rolls_into_the_next_year(tmp_path: Path) -> None:
    december = [candle(datetime(2025, 12, 31, 5, 0, tzinfo=UTC))]
    january = [candle(datetime(2026, 1, 1, 5, 0, tzinfo=UTC))]
    cache = reader(CountingSource([*december, *january]), tmp_path)

    got = await cache.get_range(
        INSTRUMENT,
        Timeframe.M5,
        datetime(2025, 12, 1, tzinfo=UTC),
        datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert got == [*december, *january]
    assert {p.name for p in tmp_path.rglob("*.parquet")} == {"2025-12.parquet", "2026-01.parquet"}


class TestCacheFiles:
    def test_a_month_lives_under_timeframe_and_instrument_with_the_colon_made_safe(
        self, tmp_path: Path
    ) -> None:
        path = CandleCacheFiles(tmp_path).path("NSE:1333", "5m", "2026-03")

        assert path == tmp_path / "5m" / "NSE_1333" / "2026-03.parquet"

    def test_status_of_an_empty_or_missing_directory_is_zero_files(self, tmp_path: Path) -> None:
        files = CandleCacheFiles(tmp_path / "not-created")

        assert files.files() == [] and files.size() == 0 and files.clear() == 0

    def test_a_write_leaves_no_temporary_file_behind(self, tmp_path: Path) -> None:
        files = CandleCacheFiles(tmp_path)
        target = files.path("NSE:1", "5m", "2026-01")

        files.write(target, b"bytes")

        assert target.read_bytes() == b"bytes"
        assert [p.name for p in target.parent.iterdir()] == ["2026-01.parquet"]
        assert files.size() == 5 and files.clear() == 1
