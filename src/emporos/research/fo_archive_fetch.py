"""Fetch the F&O bhavcopy archive politely (EM-225, PROFIT_PLAN.md §5 B-F1 and §8).

One file at a time, a few seconds apart, an honest user agent, resumable through the ledger. A
401, 403 or 429 stops the whole run at once: an access control is never worked around. A 404 is
not a
refusal, it is a day with no file (a holiday), and is recorded as such. Any other failure (a 5xx, a
timeout, a file that will not parse) is reported and the date is left unrecorded so a later run
retries it; three in a row halt the run, because that pattern is a block, not bad luck.

Newest days first: the recent contract specifications (weekly expiries, lot sizes) matter most and
the archive's oldest years matter least, so an interrupted run has kept the valuable part."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import date, timedelta

import httpx

from emporos.core.clock import Clock, Sleeper
from emporos.research.fo_archive_layout import ArchiveCandidate, ArchiveLayout
from emporos.research.fo_archive_rows import ArchiveParseError, read_archive
from emporos.research.fo_archive_store import FetchOutcome, FoDayStore, FoLedger, LedgerEntry

__all__ = [
    "ArchiveFetcher",
    "ArchiveHalted",
    "ArchiveRefused",
    "FetchReport",
    "USER_AGENT",
    "weekdays_descending",
]

USER_AGENT = "emporos-research/1.0 (personal research, one request every few seconds)"
_REFUSALS = (401, 403, 429)
_MAX_CONSECUTIVE_FAILURES = 3


class ArchiveRefused(RuntimeError):
    """The archive host refused the request (401, 403 or 429); the run must stop, not retry."""


class ArchiveHalted(RuntimeError):
    """Several failures in a row: the host looks unhealthy or is blocking. Stop and report."""


class FetchReport:
    def __init__(self) -> None:
        self.fetched = 0
        self.absent = 0
        self.skipped = 0
        self.failed: list[tuple[date, str]] = []
        self.rows = 0


def weekdays_descending(first: date, last: date) -> Iterator[date]:
    """Every Monday-to-Friday from `last` back to `first` (an exchange holiday is found by a
    404)."""
    day = last
    while day >= first:
        if day.weekday() < 5:
            yield day
        day -= timedelta(days=1)


class ArchiveFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        layout: ArchiveLayout,
        store: FoDayStore,
        ledger: FoLedger,
        clock: Clock,
        sleeper: Sleeper,
        seconds_between_requests: float = 3.0,
        progress: Callable[[str], None] = lambda line: None,
    ) -> None:
        if seconds_between_requests < 1.0:
            raise ValueError("requests must be at least a second apart")
        self._client = client
        self._layout = layout
        self._store = store
        self._ledger = ledger
        self._clock = clock
        self._sleeper = sleeper
        self._gap = seconds_between_requests
        self._progress = progress
        self._requests = 0

    async def run(self, first: date, last: date) -> FetchReport:
        report = FetchReport()
        done = self._ledger.done()
        consecutive_failures = 0
        for day in weekdays_descending(first, last):
            if day in done:
                report.skipped += 1
                continue
            try:
                await self._fetch_day(day, report)
                consecutive_failures = 0
            except (httpx.HTTPError, ArchiveParseError, OSError) as error:
                consecutive_failures += 1
                report.failed.append((day, repr(error)))
                self._progress(f"{day}: failed ({error!r})")
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    raise ArchiveHalted(
                        f"{consecutive_failures} failures in a row ending {day}: {error!r}"
                    ) from error
        return report

    async def _fetch_day(self, day: date, report: FetchReport) -> None:
        last_url = ""
        for candidate in self._layout.candidates(day):
            last_url = candidate.url
            payload = await self._get(candidate)
            if payload is None:
                continue  # 404 on this layout: try the next
            rows = read_archive(day, payload, candidate.format)
            stored = self._store.write(day, rows)
            self._ledger.record(
                LedgerEntry(
                    day,
                    FetchOutcome.FETCHED,
                    candidate.url,
                    self._clock.now(),
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                    stored,
                    candidate.format,
                )  # fmt: skip
            )
            report.fetched += 1
            report.rows += stored
            self._progress(f"{day}: {stored} index contract row(s) from {candidate.format.value}")
            return
        self._ledger.record(
            LedgerEntry(day, FetchOutcome.ABSENT, last_url, self._clock.now(), 0, "", 0, None)
        )
        report.absent += 1
        self._progress(f"{day}: no file (404)")

    async def _get(self, candidate: ArchiveCandidate) -> bytes | None:
        if self._requests:
            await self._sleeper.sleep(self._gap)
        self._requests += 1
        response = await self._client.get(candidate.url, headers={"User-Agent": USER_AGENT})
        if response.status_code in _REFUSALS:
            raise ArchiveRefused(
                f"{candidate.url}: the archive answered {response.status_code}; stopping the run"
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content
