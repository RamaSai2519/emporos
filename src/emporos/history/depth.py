"""How far back the broker's history goes, and how much of it one request will serve (EM-132).

Both answers are FOUND by asking, read-only, never assumed from documentation: the broker
truncates an over-long request SILENTLY to its most recent days, and history older than some age is
simply empty, so neither limit announces itself.

    span limit   the longest request that comes back whole. Ask for ever longer spans ending on the
                 same day; the first that comes back missing its early days is over the limit.
    depth        the oldest day with any bars. Step back a year at a time until a week comes back
                 empty, then bisect between the last year that had data and the first that did not.

The probe depends on `HistoricalCandleSource` only, so it runs against a fake in tests and the real
broker from the CLI. It stops (raises) rather than guess when an answer is not trustworthy: a
transient failure looks like "no data" and would silently shorten the finding.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from emporos.core.clock import IST
from emporos.core.errors import EmporosError, ErrorClassification
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.history.source import HistoricalCandleSource

# The finding is a BRACKET (largest whole, first truncated): a cut of a day or two hides inside the
# slack a weekend or holiday gives the request's first day, so rungs closer than that would lie.
SPAN_LADDER: tuple[int, ...] = (7, 14, 28, 30, 45, 60, 90, 100, 120, 180, 200, 365, 400, 730)
PROBE_WEEK = timedelta(days=7)  # wide enough to always contain trading days, holidays included
EARLY_TOLERANCE = timedelta(days=4)  # a request opening on a Saturday before a Monday holiday
_MAX_YEARS = 20


class ProbeInconclusiveError(RuntimeError):
    """A probe call failed in a way that does not mean "the broker has no data"."""


@dataclass(frozen=True)
class SpanFinding:
    timeframe: Timeframe
    largest_whole_days: int | None  # the longest request that came back whole (None: even 7 didn't)
    first_truncated_days: int | None  # the shortest request that came back cut short
    truncated_to_days: int | None  # how many days back that truncated request actually reached


@dataclass(frozen=True)
class DepthFinding:
    timeframe: Timeframe
    earliest_day: date | None  # the oldest IST day with bars (None: nothing found at all)
    bars_in_probe_week: int


@dataclass(frozen=True)
class TimeframeFinding:
    span: SpanFinding
    depth: DepthFinding
    calls: int


@dataclass(frozen=True)
class ProbeReport:
    instrument: str
    as_of: date
    findings: tuple[TimeframeFinding, ...] = field(default_factory=tuple)


class _CountingSource:
    """Counts the calls, so a report says what the probe cost."""

    def __init__(self, source: HistoricalCandleSource) -> None:
        self._source = source
        self.calls = 0

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls += 1
        try:
            return await self._source.fetch(instrument, timeframe, start, end)
        except EmporosError as error:
            if error.classification is ErrorClassification.DEFINITIVE:
                return []  # the broker said no: for a probe, that is "no bars there"
            raise ProbeInconclusiveError(
                f"{timeframe.value} {start.date()}..{end.date()}: {error.message}"
            ) from error


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=IST)


def _day(bar: Candle) -> date:
    return bar.ts.astimezone(IST).date()


class HistoryDepthProbe:
    def __init__(
        self,
        source: HistoricalCandleSource,
        instrument: Instrument,
        ladder: Sequence[int] = SPAN_LADDER,
    ) -> None:
        self._source = _CountingSource(source)
        self._instrument = instrument
        self._ladder = tuple(sorted(ladder))

    async def probe(self, timeframes: Sequence[Timeframe], as_of: date) -> ProbeReport:
        """`as_of` is the last day asked for (a day that has closed)."""
        findings: list[TimeframeFinding] = []
        for timeframe in timeframes:
            before = self._source.calls
            span = await self.span_limit(timeframe, as_of)
            depth = await self.depth(timeframe, as_of)
            findings.append(TimeframeFinding(span, depth, self._source.calls - before))
        return ProbeReport(self._instrument.instrument_id, as_of, tuple(findings))

    async def span_limit(self, timeframe: Timeframe, end_day: date) -> SpanFinding:
        whole: int | None = None
        for days in self._ladder:
            last = end_day + timedelta(days=1)  # the request's exclusive end
            start = last - timedelta(days=days)  # ... so it spans exactly `days` days
            bars = await self._fetch(timeframe, start, last)
            if not bars:
                return SpanFinding(timeframe, whole, days, None)  # nothing at all: cannot judge
            reached = (end_day - min(_day(b) for b in bars)).days
            if _midnight(start) + EARLY_TOLERANCE < min(b.ts for b in bars):
                return SpanFinding(timeframe, whole, days, reached)  # cut short: over the limit
            whole = days
        return SpanFinding(timeframe, whole, None, None)  # every rung came back whole

    async def depth(self, timeframe: Timeframe, end_day: date) -> DepthFinding:
        newest_with_data, oldest_without = await self._bracket(timeframe, end_day)
        if newest_with_data is None:
            return DepthFinding(timeframe, None, 0)
        if oldest_without is not None:
            newest_with_data = await self._bisect(timeframe, oldest_without, newest_with_data)
        bars = await self._week(timeframe, newest_with_data)
        return DepthFinding(timeframe, min(_day(b) for b in bars), len(bars))

    async def _bracket(
        self, timeframe: Timeframe, end_day: date
    ) -> tuple[date | None, date | None]:
        """The oldest step that still had data, and the first step that had none. The newest week
        anchors it, so a history shorter than a year is still bracketed."""
        newest = end_day - PROBE_WEEK + timedelta(days=1)
        if not await self._week(timeframe, newest):
            return None, None  # nothing even in the latest week: no data to measure
        with_data: date | None = newest
        for years in range(1, _MAX_YEARS + 1):
            day = self._years_back(end_day, years)
            if await self._week(timeframe, day):
                with_data = day
            else:
                return with_data, day
        return with_data, None

    async def _bisect(self, timeframe: Timeframe, empty: date, has_data: date) -> date:
        """Narrow (empty < has_data) to a week: the oldest probe week that still has bars."""
        while has_data - empty > PROBE_WEEK:
            middle = empty + (has_data - empty) // 2
            if await self._week(timeframe, middle):
                has_data = middle
            else:
                empty = middle
        return has_data

    async def _week(self, timeframe: Timeframe, first: date) -> list[Candle]:
        return await self._fetch(timeframe, first, first + PROBE_WEEK)

    async def _fetch(self, timeframe: Timeframe, first: date, end: date) -> list[Candle]:
        return await self._source.fetch(
            self._instrument, timeframe, _midnight(first), _midnight(end)
        )

    @staticmethod
    def _years_back(day: date, years: int) -> date:
        try:
            return day.replace(year=day.year - years)
        except ValueError:  # 29 February
            return day.replace(year=day.year - years, day=28)
