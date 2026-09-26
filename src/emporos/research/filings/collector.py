"""Collect, name by name and window by window, what the ledger does not hold yet (§8, EM-239)."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

import httpx

from emporos.core.clock import Clock
from emporos.research.filings.filing import parse_nse_filings
from emporos.research.filings.nse_source import FilingSource
from emporos.research.filings.polite import NotFound
from emporos.research.filings.raw_store import FetchLedger, FetchRecord, RawFilingStore

__all__ = ["CollectionHalted", "CollectionReport", "FilingCollector", "yearly_windows"]

MAX_CONSECUTIVE_FAILURES = 3  # three in a row is a block, not bad luck

Progress = Callable[[str], None]


def yearly_windows(first: date, last: date) -> list[tuple[date, date]]:
    """[first, last] cut at calendar-year ends: a name's whole span is a few small requests."""
    windows: list[tuple[date, date]] = []
    start = first
    while start <= last:
        end = min(date(start.year, 12, 31), last)
        windows.append((start, end))
        start = date(start.year + 1, 1, 1)
    return windows


class CollectionHalted(RuntimeError):
    """Consecutive failures that look like a block: the run stops."""


@dataclass
class CollectionReport:
    fetched: int = 0
    skipped: int = 0
    filings: int = 0
    failed: list[str] = field(default_factory=list)


class FilingCollector:
    def __init__(
        self, source: FilingSource, raw: RawFilingStore, ledger: FetchLedger, clock: Clock
    ) -> None:
        self._source = source
        self._raw = raw
        self._ledger = ledger
        self._clock = clock

    async def run(
        self, symbols: Sequence[str], windows: Sequence[tuple[date, date]], progress: Progress
    ) -> CollectionReport:
        """A refusal (`SourceRefused`) propagates and stops the run; any other failure is listed
        and the window is left unrecorded so a later run retries it. A window in the ledger whose
        reply is no longer on disk (a wiped cache) is fetched again."""
        report = CollectionReport()
        done = self._ledger.done()
        streak = 0
        for symbol in symbols:
            for first, last in windows:
                held = self._raw.path(self._source.source, symbol, first, last).exists()
                if (self._source.source, symbol, first, last) in done and held:
                    report.skipped += 1
                    continue
                url = self._source.url_for(symbol, first, last)
                try:
                    body = await self._source.fetch(symbol, first, last)
                    count = len(parse_nse_filings(body, url, self._clock.now().date()))
                except NotFound:
                    body, count = b"[]", 0
                except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError) as error:
                    report.failed.append(f"{symbol} {first}..{last}")
                    progress(f"{symbol} {first}..{last}: failed ({error})")
                    streak += 1
                    if streak >= MAX_CONSECUTIVE_FAILURES:
                        raise CollectionHalted(f"{streak} failures in a row; stopping") from error
                    continue
                streak = 0
                digest = self._raw.write(self._source.source, symbol, first, last, body)
                self._ledger.record(
                    FetchRecord(
                        self._source.source,
                        symbol,
                        first,
                        last,
                        url,
                        self._clock.now(),
                        count,
                        len(body),
                        digest,
                    )  # fmt: skip
                )
                report.fetched += 1
                report.filings += count
                progress(f"{symbol} {first}..{last}: {count} filing(s)")
        return report
