"""Read back what was collected: every ledgered window's raw reply, parsed (EM-239)."""

from __future__ import annotations

from collections.abc import Iterator

from emporos.research.filings.filing import Filing, parse_nse_filings
from emporos.research.filings.raw_store import FetchLedger, RawFilingStore

__all__ = ["CollectedFilings"]


class CollectedFilings:
    def __init__(self, raw: RawFilingStore, ledger: FetchLedger) -> None:
        self._raw = raw
        self._ledger = ledger

    def __iter__(self) -> Iterator[Filing]:
        latest = {(r.source, r.symbol, r.first, r.last): r for r in self._ledger.records()}
        for record in latest.values():  # a window fetched twice is read once, from its last fetch
            if record.source != "NSE":
                raise ValueError(f"no parser for source {record.source!r}")
            body = self._raw.read(record.source, record.symbol, record.first, record.last)
            yield from parse_nse_filings(body, record.url, record.fetched_at.date())
