"""Fetch a fixed list of public pages politely, each reply kept verbatim (PROFIT_PLAN §8; EM-244).

One request at a time through `PoliteGet` (an honest agent, a gap, a refusal stops the run), each
reply written under `<root>/<name>` and ledgered with its URL and fetch time. A page already in the
ledger is skipped, so a run resumes."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from emporos.core.clock import Clock
from emporos.research.filings.collector import CollectionHalted
from emporos.research.filings.polite import NotFound, PoliteGet
from emporos.research.filings.raw_store import FetchLedger, FetchRecord

__all__ = ["DEFAULT_CALENDAR_LEDGER", "DEFAULT_CALENDAR_RAW", "PageCollector", "PageSpec"]

DEFAULT_CALENDAR_RAW = Path.home() / ".cache" / "emporos" / "calendar" / "raw"
DEFAULT_CALENDAR_LEDGER = Path("docs/research/profit/calendar-ledger.jsonl")
MAX_CONSECUTIVE_FAILURES = 3


@dataclass(frozen=True)
class PageSpec:
    source: str  # the site, e.g. "fed"
    name: str  # the file the reply is kept under
    url: str


class PageCollector:
    def __init__(self, get: PoliteGet, root: Path, ledger: FetchLedger, clock: Clock) -> None:
        self._get, self._root, self._ledger, self._clock = get, root, ledger, clock

    async def run(self, pages: Sequence[PageSpec], progress: Callable[[str], None]) -> list[str]:
        """The pages that failed. `SourceRefused` propagates and stops the run."""
        held = {(r.source, r.symbol) for r in self._ledger.records()}
        failed: list[str] = []
        streak = 0
        for page in pages:
            if (page.source, page.name) in held:
                progress(f"{page.name}: already held")
                continue
            try:
                body = await self._get.get(page.url)
            except (NotFound, httpx.HTTPError) as error:
                failed.append(f"{page.name}: {error!r}")
                progress(f"{page.name}: FAILED {error!r}")
                streak += 1
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    raise CollectionHalted(f"{streak} failures in a row: {failed}") from error
                continue
            streak = 0
            self._write(page.name, body)
            now = self._clock.now()
            self._ledger.record(
                FetchRecord(
                    page.source,
                    page.name,
                    now.date(),
                    now.date(),
                    page.url,
                    now,
                    1,
                    len(body),
                    hashlib.sha256(body).hexdigest(),
                )  # fmt: skip
            )
            progress(f"{page.name}: {len(body)} bytes")
        return failed

    def _write(self, name: str, body: bytes) -> None:
        target = self._root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(body)
        os.replace(temporary, target)
