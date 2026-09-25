"""One instrument's 5-minute bars as plain arrays, from a loader or a cache (EM-243).

`SeriesSource` is the seam: production reads the vault-guarded, split-adjusted archive (composed in
`emporos.cli`), tests hand in arrays. Loading a name's eight years costs about 16 s, so
`CachedSeriesSource` keeps each name as an `.npz` next to a tag naming the adjustment ledger it was
built from (a different ledger is a different file)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import numpy as np

from emporos.domain.candles import Candle
from emporos.research.atlas.arrays import Floats, Ints

__all__ = ["BarSeries", "CachedSeriesSource", "CandleLoader", "CandleSeriesSource", "SeriesSource"]


@dataclass(frozen=True)
class BarSeries:
    ts: Ints  # bar start, epoch seconds UTC
    open: Floats
    close: Floats

    def __len__(self) -> int:
        return len(self.ts)


class SeriesSource(Protocol):
    def series(self, instrument_id: str) -> BarSeries: ...


class CandleLoader(Protocol):
    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]: ...


class CandleSeriesSource:
    def __init__(self, loader: CandleLoader, first: date, last: date) -> None:
        self._loader, self._first, self._last = loader, first, last

    def series(self, instrument_id: str) -> BarSeries:
        bars = sorted(self._loader.load(instrument_id, self._first, self._last), key=lambda b: b.ts)
        return BarSeries(
            np.array([int(b.ts.timestamp()) for b in bars], dtype=np.int64),
            np.array([float(b.open.amount) for b in bars], dtype=np.float64),
            np.array([float(b.close.amount) for b in bars], dtype=np.float64),
        )


class CachedSeriesSource:
    def __init__(self, inner: SeriesSource, directory: Path, tag: str) -> None:
        self._inner, self._directory, self._tag = inner, directory, tag

    def series(self, instrument_id: str) -> BarSeries:
        path = self._directory / f"{instrument_id.replace(':', '_')}-{self._tag}.npz"
        if path.exists():
            with np.load(path) as held:
                return BarSeries(held["ts"], held["open"], held["close"])
        found = self._inner.series(instrument_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp.npz")
        np.savez(temporary, ts=found.ts, open=found.open, close=found.close)
        temporary.replace(path)
        return found
