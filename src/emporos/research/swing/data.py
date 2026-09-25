"""The bars a swing simulation reads, on one price basis, with no look-ahead (EM-223).

`SwingSeries` is one instrument's daily bars twice over, session for session: the RAW bars (the
prices the exchange traded at: what a fill uses) and the ANALYSIS bars (adjusted for the ledger's
splits and bonuses, and with any artifact gap flattened: what a signal and a valuation read).
`multiplier[i]` is analysis price over raw price on session i, so a raw price is
`analysis / multiplier` and a whole-share position keeps its value across an ex-date.

An ARTIFACT session (a >= 15% open gap that is a fetch-chunk boundary of the broker's history, not a
move: see `research.gap_classes`) has its gap removed from the analysis series as if it were an
unrecorded split, so no return in the series contains it, and a signal's lookback never sees a
fake jump. A REAL gap is never passed to this class: it stays in the series and is traded through.

`AsOfView` is the only way a strategy sees prices: bars up to and including the decision session,
with no argument that could name a later one.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.research.adjustments import AdjustedSeries

__all__ = ["AsOfView", "ArtifactGaps", "SwingDataset", "SwingSeries", "SwingSeriesFactory"]


class ArtifactGaps(Protocol):
    def is_artifact(self, instrument_id: str, day: date) -> bool: ...


@dataclass(frozen=True)
class SwingSeries:
    instrument_id: str
    days: tuple[date, ...]
    raw: tuple[Candle, ...]
    analysis: tuple[Candle, ...]
    multipliers: tuple[Decimal, ...]  # analysis / raw, per session
    neutralised: tuple[date, ...]  # artifact sessions whose gap was flattened

    def __post_init__(self) -> None:
        sizes = {len(self.days), len(self.raw), len(self.analysis), len(self.multipliers)}
        if len(sizes) != 1:
            raise ValueError("a swing series' parts cover the same sessions")
        if any(later <= earlier for earlier, later in zip(self.days, self.days[1:], strict=False)):
            raise ValueError("a swing series is oldest first, one bar per session")

    def index_of(self, day: date) -> int | None:
        """The position of `day`, or None when the name did not trade that session."""
        i = bisect_right(self.days, day) - 1
        return i if i >= 0 and self.days[i] == day else None

    def last_index_on_or_before(self, day: date) -> int | None:
        i = bisect_right(self.days, day) - 1
        return i if i >= 0 else None


class SwingSeriesFactory:
    """`AdjustedSeries` and the artifact gaps to a `SwingSeries`."""

    def __init__(self, artifacts: ArtifactGaps | None = None) -> None:
        self._artifacts = artifacts

    def build(self, adjusted: AdjustedSeries) -> SwingSeries:
        raw, analysis = adjusted.raw, adjusted.adjusted
        if not raw:
            return SwingSeries("", (), (), (), (), ())
        instrument_id = raw[0].instrument_id
        days = tuple(bar.ts.astimezone(IST).date() for bar in raw)
        scales = [Decimal(1)] * len(raw)  # what each earlier bar is multiplied by, cumulatively
        flattened: list[date] = []
        if self._artifacts is not None:
            for i in range(len(raw) - 1, 0, -1):
                if self._artifacts.is_artifact(instrument_id, days[i]):
                    gap = analysis[i].open.amount / analysis[i - 1].close.amount
                    flattened.append(days[i])
                    for j in range(i):
                        scales[j] *= gap
        neutral = tuple(self._scaled(bar, s) for bar, s in zip(analysis, scales, strict=True))
        multipliers = tuple(
            n.close.amount / r.close.amount for n, r in zip(neutral, raw, strict=True)
        )
        return SwingSeries(
            instrument_id, days, tuple(raw), neutral, multipliers, tuple(reversed(flattened))
        )

    @staticmethod
    def _scaled(bar: Candle, scale: Decimal) -> Candle:
        if scale == 1:
            return bar
        return Candle(
            bar.instrument_id, bar.timeframe, bar.ts,
            Money(bar.open.amount * scale), Money(bar.high.amount * scale),
            Money(bar.low.amount * scale), Money(bar.close.amount * scale),
            bar.volume, bar.partial,
        )  # fmt: skip


class SwingDataset:
    """Every instrument's series on one calendar (the union of sessions any of them traded)."""

    def __init__(self, series: Iterable[SwingSeries]) -> None:
        self._series: dict[str, SwingSeries] = {}
        for s in series:
            if not s.days:
                continue
            if s.instrument_id in self._series:
                raise ValueError(f"{s.instrument_id} is in the dataset twice")
            self._series[s.instrument_id] = s
        self._calendar = tuple(sorted({d for s in self._series.values() for d in s.days}))
        self._position = {day: i for i, day in enumerate(self._calendar)}

    @property
    def calendar(self) -> tuple[date, ...]:
        return self._calendar

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._series))

    def series(self, instrument_id: str) -> SwingSeries:
        return self._series[instrument_id]

    def calendar_index(self, day: date) -> int:
        return self._position[day]

    def bar_on(self, instrument_id: str, day: date) -> int | None:
        """The series index of the name's bar on `day`, or None when it did not trade."""
        return self._series[instrument_id].index_of(day)


class AsOfView:
    """What a strategy may see at the close of `day`: analysis bars up to and including that
    session and nothing later. There is no argument that names a later date, so a strategy cannot
    ask for one; the dataset itself is not reachable from here."""

    def __init__(self, dataset: SwingDataset, day: date) -> None:
        self._dataset = dataset
        self._day = day

    @property
    def day(self) -> date:
        return self._day

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return self._dataset.instrument_ids

    def previous_session(self) -> date | None:
        """The calendar session before `day`, or None on the first."""
        calendar = self._dataset.calendar
        position = bisect_right(calendar, self._day) - 2
        return calendar[position] if position >= 0 else None

    def sessions(self) -> tuple[date, ...]:
        """The calendar up to and including `day`."""
        calendar = self._dataset.calendar
        return calendar[: bisect_right(calendar, self._day)]

    def history(self, instrument_id: str, sessions: int) -> Sequence[Candle]:
        """The name's last `sessions` analysis bars ending on or before `day` (fewer if it has
        fewer): oldest first, the last one the latest session it traded."""
        series = self._dataset.series(instrument_id)
        end = series.last_index_on_or_before(self._day)
        if end is None:
            return ()
        return series.analysis[max(0, end + 1 - sessions) : end + 1]

    def neutralised_within(self, instrument_id: str, sessions: int) -> bool:
        """Whether an artifact gap was flattened in the name's last `sessions` bars."""
        series = self._dataset.series(instrument_id)
        end = series.last_index_on_or_before(self._day)
        if end is None:
            return False
        window = set(series.days[max(0, end + 1 - sessions) : end + 1])
        return any(d in window for d in series.neutralised)
