"""Is the stored history fit to backtest on? (EM-132)

Each check is one small class with one question, run over one instrument's whole series; the
auditor reads the series through `CandleReader` (the only path to candles), runs every check and
collects `Finding`s into a report. Adding a check is adding a class, never editing one.

    DuplicateTimestamps          two bars at one instant
    TimestampAlignment           a bar off the timeframe's grid or outside the session
    IntradayGaps                 a market day the instrument has bars for, but not all of them
    MissingMarketDays            a market day the instrument has no bars at all for
    OvernightDiscontinuity       the open jumps from the last close by more than a limit: what an
                                 unadjusted split, bonus or a data error looks like

A "market day" is inferred from the data, not from a holiday list: a day on which at least half of
the audited instruments have bars. That needs no calendar (the stored one is empty) and cannot
disagree with the data it is judging.

Prices are UNADJUSTED broker prices: a split or bonus appears as a discontinuity, and that is what
`OvernightDiscontinuity` reports. Adjusting them is a separate decision (see the report).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.marketdata.session import SessionWindow
from emporos.persistence.candles import CandleReader

_SUPPORTED = (Timeframe.M1, Timeframe.M5, Timeframe.M15)


class Severity(StrEnum):
    ERROR = "error"  # the data is wrong
    WARNING = "warning"  # the data is incomplete or unusual; a person should look


@dataclass(frozen=True)
class Finding:
    check: str
    instrument_id: str
    day: date | None
    severity: Severity
    detail: str


@dataclass(frozen=True)
class SeriesContext:
    """What a check may know besides the bars: the days the market traded, and the session."""

    timeframe: Timeframe
    market_days: frozenset[date]
    window: SessionWindow

    @property
    def slots_per_day(self) -> int:
        minutes = (self.window.close_time.hour - self.window.open_time.hour) * 60 + (
            self.window.close_time.minute - self.window.open_time.minute
        )
        return minutes // int(self.timeframe.duration.total_seconds() // 60)


class SeriesCheck(Protocol):
    name: str

    def run(
        self, instrument_id: str, bars: Sequence[Candle], context: SeriesContext
    ) -> list[Finding]: ...


def _day(bar: Candle) -> date:
    return bar.ts.astimezone(IST).date()


def _by_day(bars: Sequence[Candle]) -> dict[date, list[Candle]]:
    days: dict[date, list[Candle]] = defaultdict(list)
    for bar in bars:
        days[_day(bar)].append(bar)
    return days


class DuplicateTimestamps:
    name = "duplicate_timestamps"

    def run(
        self, instrument_id: str, bars: Sequence[Candle], context: SeriesContext
    ) -> list[Finding]:
        seen: set[datetime] = set()
        found: list[Finding] = []
        for bar in bars:
            if bar.ts in seen:
                found.append(
                    Finding(
                        self.name,
                        instrument_id,
                        _day(bar),
                        Severity.ERROR,
                        f"two bars at {bar.ts.isoformat()}",
                    )  # fmt: skip
                )
            seen.add(bar.ts)
        return found


class TimestampAlignment:
    """A bar must start on the timeframe's grid (09:15 IST plus a whole number of bars) and inside
    the session. A bar stamped at its close, or shifted by the IST offset, fails here."""

    name = "timestamp_alignment"

    def run(
        self, instrument_id: str, bars: Sequence[Candle], context: SeriesContext
    ) -> list[Finding]:
        step = int(context.timeframe.duration.total_seconds())
        opened = context.window.open_time
        found: list[Finding] = []
        for bar in bars:
            local = bar.ts.astimezone(IST)
            seconds = (local.hour - opened.hour) * 3600 + (local.minute - opened.minute) * 60
            on_grid = local.second == 0 and local.microsecond == 0 and seconds % step == 0
            if not on_grid or not context.window.contains(bar.ts):
                found.append(
                    Finding(
                        self.name,
                        instrument_id,
                        local.date(),
                        Severity.ERROR,
                        f"bar at {local:%Y-%m-%d %H:%M:%S} IST is off the "
                        f"{context.timeframe.value} grid or outside the session",
                    )  # fmt: skip
                )
        return found


class MissingMarketDays:
    name = "missing_market_days"

    def run(
        self, instrument_id: str, bars: Sequence[Candle], context: SeriesContext
    ) -> list[Finding]:
        if not bars:
            return []
        have = {_day(bar) for bar in bars}
        first, last = min(have), max(have)
        return [
            Finding(self.name, instrument_id, day, Severity.WARNING, "no bars on a market day")
            for day in sorted(context.market_days)
            if first <= day <= last and day not in have
        ]


class IntradayGaps:
    """A market day with bars, but fewer than the session holds. The broker omits some minutes even
    for the most liquid names (so a 1m day is rarely full); at 5m a short day is more telling."""

    name = "intraday_gaps"

    def __init__(self, tolerated_missing: int = 0) -> None:
        self._tolerated = tolerated_missing

    def run(
        self, instrument_id: str, bars: Sequence[Candle], context: SeriesContext
    ) -> list[Finding]:
        full = context.slots_per_day
        found: list[Finding] = []
        for day, day_bars in sorted(_by_day(bars).items()):
            if day not in context.market_days:
                continue
            missing = full - len({b.ts for b in day_bars})
            if missing > self._tolerated:
                found.append(
                    Finding(
                        self.name,
                        instrument_id,
                        day,
                        Severity.WARNING,
                        f"{len(day_bars)} of {full} bars ({missing} missing)",
                    )  # fmt: skip
                )
        return found


class OvernightDiscontinuity:
    """The first open of a day against the previous market day's last close. Beyond `limit` (15% by
    default; NSE price bands are at most 20% for these names) it is a split, a bonus, or bad data:
    the report says which ratio, and a person decides."""

    name = "overnight_discontinuity"

    def __init__(self, limit: Decimal = Decimal("0.15")) -> None:
        self._limit = limit

    def run(
        self, instrument_id: str, bars: Sequence[Candle], context: SeriesContext
    ) -> list[Finding]:
        days = _by_day(bars)
        ordered = sorted(d for d in days if d in context.market_days)
        found: list[Finding] = []
        for previous, current in pairwise(ordered):
            last_close = max(days[previous], key=lambda b: b.ts).close.amount
            first_open = min(days[current], key=lambda b: b.ts).open.amount
            if last_close <= 0:
                continue
            ratio = first_open / last_close
            if abs(ratio - 1) >= self._limit:
                found.append(
                    Finding(
                        self.name,
                        instrument_id,
                        current,
                        Severity.WARNING,
                        f"opened at {ratio:.4f} x the previous close "
                        f"({last_close} -> {first_open})",
                    )  # fmt: skip
                )
        return found


def standard_checks() -> list[SeriesCheck]:
    return [
        DuplicateTimestamps(),
        TimestampAlignment(),
        MissingMarketDays(),
        IntradayGaps(),
        OvernightDiscontinuity(),
    ]


@dataclass(frozen=True)
class InstrumentSpan:
    instrument_id: str
    bars: int
    first_day: date | None
    last_day: date | None


@dataclass(frozen=True)
class QualityReport:
    timeframe: Timeframe
    first: date
    last: date
    market_days: int
    spans: tuple[InstrumentSpan, ...]
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    def count(self, check: str, severity: Severity | None = None) -> int:
        return sum(
            1
            for f in self.findings
            if f.check == check and (severity is None or f.severity is severity)
        )

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.ERROR)


class MarketDays:
    """The days the market traded, inferred: at least `share` of the instruments have bars."""

    def __init__(self, share: Decimal = Decimal("0.5")) -> None:
        self._share = share

    def infer(self, series: Mapping[str, Sequence[Candle]]) -> frozenset[date]:
        if not series:
            return frozenset()
        counts: dict[date, int] = defaultdict(int)
        for bars in series.values():
            for day in {_day(bar) for bar in bars}:
                counts[day] += 1
        needed = self._share * len(series)
        return frozenset(day for day, n in counts.items() if n >= needed)


class DatasetAuditor:
    def __init__(
        self,
        reader: CandleReader,
        checks: Sequence[SeriesCheck] | None = None,
        window: SessionWindow | None = None,
        market_days: MarketDays | None = None,
    ) -> None:
        self._reader = reader
        self._checks = list(checks if checks is not None else standard_checks())
        self._window = window or SessionWindow()
        self._market_days = market_days or MarketDays()

    async def audit(
        self, instrument_ids: Sequence[str], timeframe: Timeframe, first: date, last: date
    ) -> QualityReport:
        if timeframe not in _SUPPORTED:
            raise ValueError(f"the audit knows the 1m, 5m and 15m grids, not {timeframe.value}")
        start = datetime.combine(first, self._window.open_time, tzinfo=IST)
        end = datetime.combine(last + timedelta(days=1), self._window.open_time, tzinfo=IST)
        series = {
            instrument_id: await self._reader.get_range(instrument_id, timeframe, start, end)
            for instrument_id in instrument_ids
        }
        days = self._market_days.infer(series)
        context = SeriesContext(timeframe, days, self._window)
        findings = [
            finding
            for instrument_id, bars in series.items()
            for check in self._checks
            for finding in check.run(instrument_id, bars, context)
        ]
        spans = tuple(self._span(instrument_id, bars) for instrument_id, bars in series.items())
        return QualityReport(timeframe, first, last, len(days), spans, tuple(findings))

    @staticmethod
    def _span(instrument_id: str, bars: Sequence[Candle]) -> InstrumentSpan:
        if not bars:
            return InstrumentSpan(instrument_id, 0, None, None)
        days = [_day(bar) for bar in bars]
        return InstrumentSpan(instrument_id, len(bars), min(days), max(days))
