"""Global cues for the daily posture (PROFIT_PLAN §12.3, EM-239): the last US/EU session's closes.

Five daily series from a public feed (no key): S&P 500, Nasdaq Composite, USD/INR, Brent and the US
10-year yield. A `CueFeed` names one source's URLs and reply format: FRED's public CSV, or Yahoo
Finance's public chart JSON. An observation dated `d` is a US-session value: its close falls at
01:30-02:30 IST on `d + 1`, so at 09:00 IST on Indian day `D` every observation dated before `D` is
known and none dated `D` or later is. `GlobalCues.lines(day)` reads exactly that and nothing else. A
series with no observation in the last week is left out, and the first observation of the span has
no previous one to move from (nothing before 2024-01-01 is fetched), so those days carry fewer
lines.

The point-in-time claim is about when the VALUE was knowable in the market (the close), not about
when the archive published it (FRED posts USD/INR and Brent weekly). The feeds are historical
archives only; a live run needs a live feed for the same five numbers.

`CollectionHalted` (three failures in a row) and `SourceRefused` (401/403/429, or a reply that is
not data at all: a consent or JavaScript page) stop the run: the source is never worked around (§8).
Every reply is kept verbatim, and the ledger carries the source URL and the fetch date."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

import httpx

from emporos.core.clock import Clock
from emporos.core.paths import research_dir
from emporos.research.filings.collector import CollectionHalted
from emporos.research.filings.polite import NotFound, PoliteGet, SourceRefused
from emporos.research.filings.raw_store import FetchLedger, FetchRecord

__all__ = [
    "CUES", "DEFAULT_CUES_LEDGER", "DEFAULT_CUES_RAW_DIR", "FEEDS", "FRED", "YAHOO", "CueCollector",
    "CueFeed", "CueSeries", "CueSpec", "CueTransform", "GlobalCues", "LevelAndBasisPoints",
    "PercentChange", "parse_fred_csv", "parse_yahoo_chart",
]  # fmt: skip

DEFAULT_CUES_RAW_DIR = research_dir() / "global-cues" / "raw"
DEFAULT_CUES_LEDGER = Path("docs/research/profit/global-cues-ledger.jsonl")
MAX_AGE_DAYS = 6  # a latest observation older than this (before the decision day) is stale
MAX_CONSECUTIVE_FAILURES = 3


class CueTransform(Protocol):
    def lines(self, prefix: str, latest: float, previous: float) -> dict[str, float]: ...


class PercentChange:
    """`<prefix>_prev_close_pct`: the latest close over the one before it, in percent."""

    def lines(self, prefix: str, latest: float, previous: float) -> dict[str, float]:
        return {f"{prefix}_prev_close_pct": (latest / previous - 1) * 100} if previous else {}


class LevelAndBasisPoints:
    """A yield: `<prefix>_level` (percent) and `<prefix>_change_bp` (basis points)."""

    def lines(self, prefix: str, latest: float, previous: float) -> dict[str, float]:
        return {f"{prefix}_level": latest, f"{prefix}_change_bp": (latest - previous) * 100}


@dataclass(frozen=True)
class CueSpec:
    prefix: str  # the key prefix: sp500, nasdaq, usdinr, brent, us10y
    transform: CueTransform


CUES = (
    CueSpec("sp500", PercentChange()),
    CueSpec("nasdaq", PercentChange()),
    CueSpec("usdinr", PercentChange()),
    CueSpec("brent", PercentChange()),
    CueSpec("us10y", LevelAndBasisPoints()),
)


def parse_fred_csv(
    text: str, first: date | None = None, last: date | None = None
) -> list[tuple[date, float]]:
    """(date, value) rows of a FRED CSV, oldest first. A missing value ('.' or empty), a row that
    does not parse and a row outside [first, last] are dropped."""
    rows = csv.reader(io.StringIO(text))
    next(rows, None)  # the header: observation_date (or DATE), then the series id
    out: dict[date, float] = {}
    for row in rows:
        if len(row) < 2:
            continue
        try:
            day, value = date.fromisoformat(row[0].strip()), float(row[1])
        except ValueError:
            continue
        if (first is None or day >= first) and (last is None or day <= last):
            out[day] = value
    return sorted(out.items())


class CueSeries:
    def __init__(self, observations: Sequence[tuple[date, float]]) -> None:
        self._observations = sorted(observations)

    def last_two_before(
        self, day: date
    ) -> tuple[tuple[date, float], tuple[date, float] | None] | None:
        """The latest observation dated strictly before `day` and the one before it, or None."""
        known = [o for o in self._observations if o[0] < day]
        if not known:
            return None
        return known[-1], (known[-2] if len(known) > 1 else None)


def parse_yahoo_chart(
    text: str, first: date | None = None, last: date | None = None
) -> list[tuple[date, float]]:
    """(session date, close) rows of a Yahoo chart reply (`chart.result[0]`: `timestamp`,
    `indicators.quote[0].close`, `meta.gmtoffset`). The date is the exchange-local date of the
    bar's timestamp. A null close, a malformed reply and a row outside [first, last] are dropped."""
    try:
        result = json.loads(text)["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        offset = int(result.get("meta", {}).get("gmtoffset", 0))
    except (ValueError, KeyError, IndexError, TypeError):
        return []
    out: dict[date, float] = {}
    for stamp, close in zip(stamps, closes, strict=False):
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp + offset, UTC).date()
        if (first is None or day >= first) and (last is None or day <= last):
            out[day] = float(close)
    return sorted(out.items())


@dataclass(frozen=True)
class CueFeed:
    """One source: its name, the id it uses for each cue prefix, the URL of a window and how to
    read the reply."""

    name: str
    ids: Mapping[str, str]  # prefix -> the source's series id or symbol
    url: Callable[[str, date, date], str]
    parse: Callable[[str, date | None, date | None], list[tuple[date, float]]]
    extension: str

    def file(self, root: Path, prefix: str) -> Path:
        safe = self.ids[prefix].replace("^", "_").replace("=", "_")
        return root / self.name / f"{safe}.{self.extension}"


def _yahoo_url(symbol: str, first: date, last: date) -> str:
    start = datetime.combine(first, time(0), tzinfo=UTC)
    # One second short of the next midnight: a bar stamped at that midnight is the next day's.
    end = datetime.combine(last + timedelta(days=1), time(0), tzinfo=UTC) - timedelta(seconds=1)
    return (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={int(start.timestamp())}&period2={int(end.timestamp())}&interval=1d"
    )


FRED = CueFeed(
    "fred",
    {"sp500": "SP500", "nasdaq": "NASDAQCOM", "usdinr": "DEXINUS", "brent": "DCOILBRENTEU",
     "us10y": "DGS10"},
    lambda series, first, last: (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        f"&cosd={first.isoformat()}&coed={last.isoformat()}"
    ),
    parse_fred_csv,
    "csv",
)  # fmt: skip
YAHOO = CueFeed(
    "yahoo",
    {"sp500": "^GSPC", "nasdaq": "^IXIC", "usdinr": "INR=X", "brent": "BZ=F", "us10y": "^TNX"},
    _yahoo_url,
    parse_yahoo_chart,
    "json",
)  # fmt: skip
FEEDS = {feed.name: feed for feed in (FRED, YAHOO)}


class GlobalCues:
    """The five cues as of the morning of an Indian trading day."""

    def __init__(self, series: Mapping[str, CueSeries], specs: Sequence[CueSpec] = CUES) -> None:
        self._series = dict(series)  # by prefix
        self._specs = tuple(specs)

    def lines(self, day: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for spec in self._specs:
            found = self._series.get(spec.prefix)
            pair = found.last_two_before(day) if found is not None else None
            if pair is None or pair[1] is None:
                continue
            (seen, latest), (_, previous) = pair[0], pair[1]
            if (day - seen).days > MAX_AGE_DAYS:
                continue
            out.update(spec.transform.lines(spec.prefix, latest, previous))
        return out

    @staticmethod
    def load(
        root: Path, first: date, last: date, feed: CueFeed, specs: Sequence[CueSpec] = CUES
    ) -> GlobalCues:
        """The cues from the raw replies the collector kept under `root` (a missing file is a
        series with no observations)."""
        series: dict[str, CueSeries] = {}
        for spec in specs:
            path = feed.file(root, spec.prefix)
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            series[spec.prefix] = CueSeries(feed.parse(text, first, last))
        return GlobalCues(series, specs)


Progress = Callable[[str], None]


class CueCollector:
    """Fetch each series once over [first, last], keep the raw reply and ledger it (resumable)."""

    def __init__(
        self, get: PoliteGet, root: Path, ledger: FetchLedger, clock: Clock, feed: CueFeed
    ) -> None:
        self._get, self._root, self._ledger, self._clock = get, root, ledger, clock
        self._feed = feed

    async def run(
        self, specs: Sequence[CueSpec], first: date, last: date, progress: Progress
    ) -> list[str]:
        """The series that failed. `SourceRefused` propagates and stops the run: a refusal, or a
        reply that is no data at all (a consent or JavaScript page)."""
        done = self._ledger.done()
        failed: list[str] = []
        streak = 0
        for spec in specs:
            series_id = self._feed.ids[spec.prefix]
            if (self._feed.name, series_id, first, last) in done:
                progress(f"{series_id}: already held")
                continue
            url = self._feed.url(series_id, first, last)
            try:
                body = await self._get.get(url)
            except (NotFound, httpx.HTTPError) as error:
                failed.append(f"{series_id}: {error!r}")
                progress(f"{series_id}: FAILED {error!r}")
                streak += 1
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    raise CollectionHalted(f"{streak} failures in a row: {failed}") from error
                continue
            streak = 0
            count = len(self._feed.parse(body.decode("utf-8", "replace"), None, None))
            if count == 0:
                raise SourceRefused(f"{url}: the reply holds no observations ({body[:60]!r})")
            self._write(spec.prefix, body)
            self._ledger.record(
                FetchRecord(
                    self._feed.name,
                    series_id,
                    first,
                    last,
                    url,
                    self._clock.now(),
                    count,
                    len(body),
                    hashlib.sha256(body).hexdigest(),
                )  # fmt: skip
            )
            progress(f"{series_id}: {count} observations")
        return failed

    def _write(self, prefix: str, body: bytes) -> None:
        target = self._feed.file(self._root, prefix)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(body)
        os.replace(temporary, target)
