"""Daily bars for the swing track, built from the 5m bars on disk (PROFIT_PLAN A-F1, EM-220).

A daily bar is the regular session's open (first 5m open), high, low, close (last 5m close) and
volume (sum), dated by the IST session day. Its `ts` is that day's midnight IST, the same instant
the broker's own daily bars carry, so a reader cannot tell which source made one.

Three rules, each a fact and not a tuning knob:

* **Regular hours only.** A bar that starts outside 09:15-15:30 IST (the Muhurat evening hour, a
  stray pre-open print) is dropped and counted; it never moves a session's high, low or close.
* **`partial` means the source said so.** A 5m bar is written only when something traded, so an
  illiquid name has no 09:15 or 15:25 bar on many days; its open is then the first trade and its
  close the last, which is the right daily bar, not a damaged one. Such days are listed apart
  (`thin_edge_days`) so a reader can size them. A session is `partial` only if a 5m bar in it was
  (a feed gap).
* **Nothing is adjusted.** These are raw prices. Splits and bonuses are the adjuster's job (A-F2).

`DailyBarBuilder` reads through the `BarSource` seam, one instrument and one split at a time, so
the caller decides which names (the manifest's `included`, never the holdout) and which days (the
vault reader refuses the sealed ones). It writes month files in the cache's own layout and codec.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.persistence.candle_cold import ParquetCandleCodec
from emporos.research.cell_run import BarSource
from emporos.research.partition import DataSplit

__all__ = ["BuildReport", "DailyBarBuilder", "DailyBarStore", "DailySeries", "SessionAggregator"]

SESSION_OPEN = time(9, 15)
LAST_BAR_START = time(15, 25)  # the last 5m bar of a regular session starts here
SESSION_CLOSE = time(15, 30)


@dataclass(frozen=True)
class DailySeries:
    """One instrument's daily bars over what was read, and what was set aside on the way."""

    bars: tuple[Candle, ...]
    partial_days: tuple[date, ...]  # sessions with a feed-gap (partial) 5m bar
    thin_edge_days: tuple[date, ...]  # sessions with no trade in the 09:15 or the 15:25 slot
    off_hours_dropped: int  # 5m bars that started outside regular hours


class SessionAggregator:
    """5m bars to daily bars. Pure: no reads, no clock."""

    def aggregate(self, instrument_id: str, bars: Iterable[Candle]) -> DailySeries:
        sessions: dict[date, list[Candle]] = defaultdict(list)
        dropped = 0
        for bar in bars:
            local = bar.ts.astimezone(IST)
            if not SESSION_OPEN <= local.time() < SESSION_CLOSE:
                dropped += 1
                continue
            sessions[local.date()].append(bar)
        daily: list[Candle] = []
        partial_days: list[date] = []
        thin_edge_days: list[date] = []
        for day in sorted(sessions):
            ordered = sorted({b.ts: b for b in sessions[day]}.values(), key=lambda b: b.ts)
            bar = self._session_bar(instrument_id, day, ordered)
            daily.append(bar)
            if bar.partial:
                partial_days.append(day)
            if self._has_thin_edge(ordered):
                thin_edge_days.append(day)
        return DailySeries(tuple(daily), tuple(partial_days), tuple(thin_edge_days), dropped)

    @staticmethod
    def _has_thin_edge(ordered: Sequence[Candle]) -> bool:
        return (
            ordered[0].ts.astimezone(IST).time() != SESSION_OPEN
            or ordered[-1].ts.astimezone(IST).time() != LAST_BAR_START
        )

    @staticmethod
    def _session_bar(instrument_id: str, day: date, ordered: Sequence[Candle]) -> Candle:
        first, last = ordered[0], ordered[-1]
        return Candle(
            instrument_id,
            Timeframe.D1,
            datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC),
            first.open,
            max(b.high for b in ordered),
            min(b.low for b in ordered),
            last.close,
            sum(b.volume for b in ordered),
            partial=any(b.partial for b in ordered),
        )


class DailyBarStore:
    """Month files of daily bars in the cache's layout (`{root}/1d/{id}/{YYYY-MM}.parquet`, UTC
    months of the bar's `ts`), written whole and atomically. A rebuild replaces a month: the
    builder's output is a pure function of the 5m bars, so there is nothing to merge."""

    def __init__(self, files: CandleCacheFiles, codec: ParquetCandleCodec | None = None) -> None:
        self._files = files
        self._codec = codec or ParquetCandleCodec()

    def write(self, instrument_id: str, bars: Sequence[Candle]) -> int:
        """Write `bars` (one instrument, daily); the number of month files written."""
        months: dict[str, list[Candle]] = defaultdict(list)
        for bar in bars:
            if bar.instrument_id != instrument_id or bar.timeframe is not Timeframe.D1:
                raise ValueError("the store takes one instrument's daily bars only")
            months[f"{bar.ts:%Y-%m}"].append(bar)
        for month, month_bars in months.items():
            path = self._files.path(instrument_id, Timeframe.D1.value, month)
            self._files.write(path, self._codec.encode(sorted(month_bars, key=lambda b: b.ts)))
        return len(months)


@dataclass(frozen=True)
class BuildReport:
    instrument_id: str
    sessions: int
    partial_days: int
    thin_edge_days: int
    off_hours_dropped: int
    files_written: int
    first: date | None
    last: date | None


class DailyBarBuilder:
    """Reads an instrument's 5m bars split by split, aggregates, and stores the daily series."""

    def __init__(
        self,
        source: BarSource,
        store: DailyBarStore,
        splits: Sequence[DataSplit],
        aggregator: SessionAggregator | None = None,
    ) -> None:
        if not splits:
            raise ValueError("a build needs at least one split to read")
        self._source = source
        self._store = store
        self._splits = tuple(splits)
        self._aggregator = aggregator or SessionAggregator()

    def build(self, instrument_id: str) -> BuildReport:
        bars: list[Candle] = []
        partial: list[date] = []
        thin: list[date] = []
        dropped = 0
        for split in self._splits:
            series = self._aggregator.aggregate(
                instrument_id, self._source.bars(instrument_id, split)
            )
            bars.extend(series.bars)
            partial.extend(series.partial_days)
            thin.extend(series.thin_edge_days)
            dropped += series.off_hours_dropped
        written = self._store.write(instrument_id, bars) if bars else 0
        days = [b.ts.astimezone(IST).date() for b in bars]
        return BuildReport(
            instrument_id, len(bars), len(partial), len(thin), dropped, written,
            days[0] if days else None, days[-1] if days else None,
        )  # fmt: skip

    def build_all(self, instrument_ids: Iterable[str]) -> list[BuildReport]:
        return [self.build(instrument_id) for instrument_id in instrument_ids]
