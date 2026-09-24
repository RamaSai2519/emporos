"""A local read-through cache of closed candle months, for backtests that read the same bars again
and again (a walk-forward reads every window's bars once per candidate).

`CachingCandleReader` wraps any `CandleReader` (in practice the `CandleRepository`, so the hot/cold
split stays hidden and nothing reaches the `candles` collection directly). A UTC calendar month
that ended long enough ago to be final is fetched from the source ONCE, written as one Parquet file
(`{root}/{timeframe}/{instrument}/{YYYY-MM}.parquet`, the cold tier's own codec) and served from
that file, and from memory after the first read in a process, from then on.

What is never cached: a month that is still open or ended too recently to be final (bars can still
arrive), and a month the source returns EMPTY (a gap that a later backfill may fill; remembering
"nothing" would hide the fill). A cache file is a derived copy of what the source held when it was
written: bars stored later for an already-cached month do not appear until the file is removed
(`clear`). `fingerprint` names exactly which files exist, for recording with a run.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from emporos.core.clock import Clock
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cold import ParquetCandleCodec
from emporos.persistence.candles import CandleReader

FINAL_AFTER = timedelta(days=2)  # a month is final this long after it ends


def utc_months(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """The UTC calendar months that overlap `[start, end)`, each as (first instant, next month)."""
    months: list[tuple[datetime, datetime]] = []
    cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
    while cursor < end:
        following = datetime(
            cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1, tzinfo=UTC
        )
        months.append((cursor, following))
        cursor = following
    return months


class CandleCacheFiles:
    """The cache's files on disk: where a month lives, atomic writes, and what is there."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def path(self, instrument_id: str, timeframe: str, month: str) -> Path:
        return self._root / timeframe / instrument_id.replace(":", "_") / f"{month}.parquet"

    def write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)  # atomic: a reader never sees half a file

    def files(self) -> list[Path]:
        return sorted(self._root.rglob("*.parquet")) if self._root.is_dir() else []

    def size(self) -> int:
        return sum(path.stat().st_size for path in self.files())

    def clear(self) -> int:
        """Delete every cached file; the count removed."""
        files = self.files()
        for path in files:
            path.unlink()
        return len(files)

    def fingerprint(self) -> str:
        """A short hash of the files' names and sizes: which bars a run could have read."""
        digest = hashlib.sha256()
        for path in self.files():
            digest.update(f"{path.relative_to(self._root)}:{path.stat().st_size};".encode())
        return f"candle-cache:{digest.hexdigest()[:16]}"


class ColdArchiveFiles(CandleCacheFiles):
    """The local cold tier as a read-only layer for `FileCandleReader`: the archive's own layout
    (`{root}/candles/{timeframe}/{instrument id}/{YYYY-MM}.parquet`, the id keeping its colon) and
    the same codec, so a wide-universe run reads the months the fetcher archived without a
    database and without copying them into the cache first. Never written through."""

    def path(self, instrument_id: str, timeframe: str, month: str) -> Path:
        return self.root / "candles" / timeframe / instrument_id / f"{month}.parquet"

    def write(self, path: Path, data: bytes) -> None:
        raise PermissionError("the cold archive is read-only here: the fetcher owns its files")

    def clear(self) -> int:
        raise PermissionError("the cold archive is read-only here: it is the only copy of history")


class CachingCandleReader:
    def __init__(
        self,
        source: CandleReader,
        files: CandleCacheFiles,
        clock: Clock,
        codec: ParquetCandleCodec | None = None,
        final_after: timedelta = FINAL_AFTER,
    ) -> None:
        self._source = source
        self._files = files
        self._clock = clock
        self._codec = codec or ParquetCandleCodec()
        self._final_after = final_after
        self._memory: dict[tuple[str, str, str], list[Candle]] = {}

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Bars with `start <= ts < end`, oldest first, one per timestamp."""
        if start >= end:
            return []
        found: list[Candle] = []
        for month_start, month_end in utc_months(start, end):
            bars = await self._month(instrument_id, timeframe, month_start, month_end)
            found.extend(c for c in bars if start <= c.ts < end)
        return found

    def clear(self) -> int:
        """Delete every cached file (and forget what is in memory); the count removed."""
        self._memory.clear()
        return self._files.clear()

    def fingerprint(self) -> str:
        return self._files.fingerprint()

    async def _month(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        key = (instrument_id, timeframe.value, f"{start:%Y-%m}")
        if key in self._memory:
            return self._memory[key]
        path = self._files.path(*key)
        if path.is_file():
            bars = self._codec.decode(path.read_bytes())
            self._memory[key] = bars
            return bars
        bars = await self._source.get_range(instrument_id, timeframe, start, end)
        if bars and self._is_final(end):
            self._files.write(path, self._codec.encode(bars))
            self._memory[key] = bars
        return bars

    def _is_final(self, month_end: datetime) -> bool:
        return month_end + self._final_after <= self._clock.now()


class FileCandleReader:
    """Reads bars ONLY from month files (the cache, and any snapshot beside it); it has no source
    to fall back on, so it can never reach the database. A month with no file reads as empty.

    Meant for backtest worker processes, whose files were put in place beforehand by a
    `CandlePrefetcher`. A file that is there is complete for its month, so serving from the first
    layer that has one is safe.
    """

    def __init__(
        self,
        layers: Sequence[CandleCacheFiles],
        codec: ParquetCandleCodec | None = None,
        *,
        memoize: bool = True,
    ) -> None:
        """`memoize=False` keeps no month in memory after it is returned: a run that walks a wide
        universe once, name by name, would otherwise hold every name's decade of bars at once."""
        self._layers = tuple(layers)
        self._codec = codec or ParquetCandleCodec()
        self._memoize = memoize
        self._memory: dict[tuple[str, str, str], list[Candle]] = {}

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Bars with `start <= ts < end`, oldest first, one per timestamp."""
        if start >= end:
            return []
        found: list[Candle] = []
        for month_start, _ in utc_months(start, end):
            bars = self._month(instrument_id, timeframe.value, f"{month_start:%Y-%m}")
            found.extend(c for c in bars if start <= c.ts < end)
        return found

    def _month(self, instrument_id: str, timeframe: str, month: str) -> list[Candle]:
        key = (instrument_id, timeframe, month)
        if not self._memoize:
            return self._read(key)
        if key not in self._memory:
            self._memory[key] = self._read(key)
        return self._memory[key]

    def _read(self, key: tuple[str, str, str]) -> list[Candle]:
        for layer in self._layers:
            path = layer.path(*key)
            if path.is_file():
                return self._codec.decode(path.read_bytes())
        return []


@dataclass(frozen=True)
class CandleNeed:
    """Bars of one instrument and timeframe over `[start, end)` that a run will read."""

    instrument_id: str
    timeframe: Timeframe
    start: datetime
    end: datetime


class CandlePrefetcher:
    """Makes every month a set of runs will read available as a FILE, before any worker starts.

    A month already in the cache (or in the snapshot) is left alone. Any other is read once, here,
    through the caching reader: a closed month lands in the cache; a month too recent to cache is
    written to the SNAPSHOT instead (a run-scoped directory the caller owns and removes), which
    also freezes that month for the whole batch. A month the source has nothing for is remembered
    as empty and not asked about again.

    Reads are made a few at a time, because the shared Atlas stalls under many large ones.
    """

    def __init__(
        self,
        reader: CandleReader,
        cache: CandleCacheFiles,
        snapshot: CandleCacheFiles,
        concurrency: int = 2,
        codec: ParquetCandleCodec | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("prefetch needs at least one read at a time")
        self._reader = reader
        self._cache = cache
        self._snapshot = snapshot
        self._codec = codec or ParquetCandleCodec()
        self._concurrency = concurrency
        self._empty: set[tuple[str, str, str]] = set()

    async def ensure(self, needs: Iterable[CandleNeed]) -> None:
        months = sorted(
            {
                (need.instrument_id, need.timeframe, start, end)
                for need in needs
                for start, end in utc_months(need.start, need.end)
            },
            key=lambda m: (m[0], m[1].value, m[2]),
        )
        gate = asyncio.Semaphore(self._concurrency)

        async def one(month: tuple[str, Timeframe, datetime, datetime]) -> None:
            async with gate:
                await self._ensure_month(*month)

        await asyncio.gather(*(one(m) for m in months))

    async def _ensure_month(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> None:
        key = (instrument_id, timeframe.value, f"{start:%Y-%m}")
        if key in self._empty or self._cache.path(*key).is_file():
            return
        if self._snapshot.path(*key).is_file():
            return
        bars = await self._reader.get_range(instrument_id, timeframe, start, end)
        if not bars:
            self._empty.add(key)
        elif not self._cache.path(*key).is_file():  # too recent to have been cached by the read
            self._snapshot.write(self._snapshot.path(*key), self._codec.encode(bars))
