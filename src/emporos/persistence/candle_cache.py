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

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from emporos.core.clock import Clock
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cold import ParquetCandleCodec
from emporos.persistence.candles import CandleReader

FINAL_AFTER = timedelta(days=2)  # a month is final this long after it ends


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
        for month_start, month_end in self._months(start, end):
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

    @staticmethod
    def _months(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        months: list[tuple[datetime, datetime]] = []
        cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
        while cursor < end:
            following = datetime(
                cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1, tzinfo=UTC
            )
            months.append((cursor, following))
            cursor = following
        return months
