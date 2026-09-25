"""Global cues for the daily posture (PROFIT_PLAN §12.3, EM-239): the last US/EU session's closes.

Five daily series from FRED's public CSV (no key): S&P 500, Nasdaq Composite, USD/INR, Brent and the
US 10-year yield. An observation dated `d` is a US-session value: its close falls at 01:30-02:30 IST
on `d + 1`, so at 09:00 IST on Indian day `D` every observation dated before `D` is known and none
dated `D` or later is. `GlobalCues.lines(day)` reads exactly that and nothing else. A series with no
observation in the last week is left out, and the first observation of the span has no previous one
to move from (nothing before 2024-01-01 is fetched), so those days simply carry fewer lines.

The point-in-time claim is about when the VALUE was knowable in the market (the close), not about
when FRED published it. FRED is the historical archive only; a live run needs a live feed for the
same five numbers.

`CollectionHalted` (three failures in a row) and `SourceRefused` (401/403/429) stop the run: the
source is never worked around (§8). Every reply is kept verbatim, and the ledger carries the source
URL and the fetch date."""

from __future__ import annotations

import csv
import hashlib
import io
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import httpx

from emporos.core.clock import Clock
from emporos.research.filings.collector import CollectionHalted
from emporos.research.filings.polite import NotFound, PoliteGet
from emporos.research.filings.raw_store import FetchLedger, FetchRecord

__all__ = [
    "CUES", "DEFAULT_CUES_LEDGER", "DEFAULT_CUES_RAW_DIR", "CueCollector", "CueSeries", "CueSpec",
    "CueTransform", "GlobalCues", "LevelAndBasisPoints", "PercentChange", "parse_fred_csv",
]  # fmt: skip

DEFAULT_CUES_RAW_DIR = Path.home() / ".cache" / "emporos" / "global-cues" / "raw"
DEFAULT_CUES_LEDGER = Path("docs/research/profit/global-cues-ledger.jsonl")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={first}&coed={last}"
SOURCE = "fred"
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
    series_id: str
    prefix: str
    transform: CueTransform


CUES = (
    CueSpec("SP500", "sp500", PercentChange()),
    CueSpec("NASDAQCOM", "nasdaq", PercentChange()),
    CueSpec("DEXINUS", "usdinr", PercentChange()),
    CueSpec("DCOILBRENTEU", "brent", PercentChange()),
    CueSpec("DGS10", "us10y", LevelAndBasisPoints()),
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


class GlobalCues:
    """The five cues as of the morning of an Indian trading day."""

    def __init__(self, series: Mapping[str, CueSeries], specs: Sequence[CueSpec] = CUES) -> None:
        self._series = dict(series)
        self._specs = tuple(specs)

    def lines(self, day: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for spec in self._specs:
            found = self._series.get(spec.series_id)
            pair = found.last_two_before(day) if found is not None else None
            if pair is None or pair[1] is None:
                continue
            (seen, latest), (_, previous) = pair[0], pair[1]
            if (day - seen).days > MAX_AGE_DAYS:
                continue
            out.update(spec.transform.lines(spec.prefix, latest, previous))
        return out

    @staticmethod
    def load(root: Path, first: date, last: date, specs: Sequence[CueSpec] = CUES) -> GlobalCues:
        """The cues from the raw replies the collector kept under `root` (a missing file is a
        series with no observations)."""
        series: dict[str, CueSeries] = {}
        for spec in specs:
            path = root / f"{spec.series_id}.csv"
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            series[spec.series_id] = CueSeries(parse_fred_csv(text, first, last))
        return GlobalCues(series, specs)


Progress = Callable[[str], None]


class CueCollector:
    """Fetch each series once over [first, last], keep the raw CSV and ledger it (resumable)."""

    def __init__(self, get: PoliteGet, root: Path, ledger: FetchLedger, clock: Clock) -> None:
        self._get, self._root, self._ledger, self._clock = get, root, ledger, clock

    async def run(
        self, specs: Sequence[CueSpec], first: date, last: date, progress: Progress
    ) -> list[str]:
        """The series that failed; `SourceRefused` propagates and stops the run."""
        done = self._ledger.done()
        failed: list[str] = []
        streak = 0
        for spec in specs:
            if (SOURCE, spec.series_id, first, last) in done:
                progress(f"{spec.series_id}: already held")
                continue
            url = FRED_URL.format(id=spec.series_id, first=first.isoformat(), last=last.isoformat())
            try:
                body = await self._get.get(url)
            except (NotFound, httpx.HTTPError) as error:
                failed.append(f"{spec.series_id}: {error!r}")
                progress(f"{spec.series_id}: FAILED {error!r}")
                streak += 1
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    raise CollectionHalted(f"{streak} failures in a row: {failed}") from error
                continue
            streak = 0
            count = len(parse_fred_csv(body.decode("utf-8", "replace")))
            self._write(spec.series_id, body)
            self._ledger.record(
                FetchRecord(
                    SOURCE,
                    spec.series_id,
                    first,
                    last,
                    url,
                    self._clock.now(),
                    count,
                    len(body),
                    hashlib.sha256(body).hexdigest(),
                )  # fmt: skip
            )
            progress(f"{spec.series_id}: {count} observations")
        return failed

    def _write(self, series_id: str, body: bytes) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"{series_id}.csv"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(body)
        os.replace(temporary, target)
