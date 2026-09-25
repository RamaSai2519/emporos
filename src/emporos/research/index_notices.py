"""NSE Indices press releases: the listing, the candidates, a polite collector (EM-224, A-F3).

niftyindices.com posts every index notice as a PDF under `/Press_Release/`, and one public page
(`/press-release`) lists them all with their date and title. The operator ruled on 2026-09-25
(PROFIT_PLAN §8) that public data may be collected for this private research. The rules that ruling
sets are kept here as code: an honest user agent, one request at a time at least a second apart
(three by default), nothing retried, a refusal (401, 403, 429) stops the run, and every file is
recorded with its URL, fetch date and content hash. Run it from the development machine only.

Which notices matter is decided from the TITLE alone: a notice that can change a constituent list
(a replacement, a change, an exclusion), not one about fixed income, IPO, SME, ESG or a corporate
action adjustment. The constituents of NIFTY 50, NIFTY Next 50 and NIFTY Midcap 150 are inside
generic "Replacements in indices" notices, so every such notice is fetched and read.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import httpx

from emporos.core.clock import Sleeper

__all__ = [
    "BASE", "IndexNoticeCollector", "IndexNoticeSource", "NoticeRef", "NoticeRefused",
    "NseIndicesSource", "NoticeStore", "parse_listing", "select_candidates",
]  # fmt: skip

BASE = "https://www.niftyindices.com"
LISTING_URL = f"{BASE}/press-release"
USER_AGENT = "emporos-research/1.0 (personal research, one request every few seconds)"
FIRST_DAY = date(2016, 9, 1)
_ITEM = re.compile(
    r"data-date=\"([^\"]+)\".*?<a href='(/Press_Release/[^']+\.pdf)'[^>]*>([^<]+)</a>", re.DOTALL
)
_RELEVANT = re.compile(
    r"replacement|change|exclusion|inclusion|reconstitution|constituent|deferment|rebalancing", re.I
)
_IRRELEVANT = re.compile(
    r"fixed income|nifty ipo|sme emerge|esg|g-sec|waves|india fpi|corporate action", re.I
)
# A notice that only revises a criterion or a methodology changes no list; but the periodic reviews
# are titled "Replacements in indices and revision in criteria", and those ARE the list changes, so
# the words exclude a title only when it names no list change (a replacement, a
# reconstitution, a deferment or a rebalancing); EM-224: they hid 16 notices, among them 13 reviews
# and the COVID deferment of the March 2020 review.
_RULES_ONLY = re.compile(r"methodology|criteria", re.I)
_LIST_CHANGE = re.compile(r"replacement|reconstitution|deferment|rebalancing", re.I)


@dataclass(frozen=True)
class NoticeRef:
    notice_date: date
    url: str  # absolute
    title: str

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


class NoticeRefused(RuntimeError):
    """The site declined to serve a request; the run must stop, not retry."""


def parse_listing(page: str) -> list[NoticeRef]:
    """Every notice on the listing page, newest first as posted. Titles are unescaped."""
    refs = [
        NoticeRef(
            datetime.strptime(stamp, "%b %d, %Y").date(),
            BASE + path,
            " ".join(html.unescape(title).split()),
        )
        for stamp, path, title in _ITEM.findall(page)
    ]
    return sorted(refs, key=lambda r: (r.notice_date, r.url), reverse=True)


def select_candidates(refs: Sequence[NoticeRef], since: date = FIRST_DAY) -> list[NoticeRef]:
    """Notices from `since` whose title says a constituent list may have changed, oldest first."""
    chosen = [
        r for r in refs
        if r.notice_date >= since and _RELEVANT.search(r.title) and not _IRRELEVANT.search(r.title)
        and (_LIST_CHANGE.search(r.title) or not _RULES_ONLY.search(r.title))
    ]  # fmt: skip
    return sorted(chosen, key=lambda r: (r.notice_date, r.url))


class IndexNoticeSource(Protocol):
    async def listing(self) -> str: ...

    async def pdf(self, url: str) -> bytes: ...


class NseIndicesSource:
    def __init__(
        self, client: httpx.AsyncClient, sleeper: Sleeper, seconds_between_requests: float = 3.0
    ) -> None:
        if seconds_between_requests < 1.0:
            raise ValueError("requests must be at least a second apart")
        self._client = client
        self._sleeper = sleeper
        self._gap = seconds_between_requests
        self._requests = 0

    async def listing(self) -> str:
        return (await self._get(LISTING_URL)).text

    async def pdf(self, url: str) -> bytes:
        return (await self._get(url)).content

    async def _get(self, url: str) -> httpx.Response:
        if self._requests:
            await self._sleeper.sleep(self._gap)
        self._requests += 1
        response = await self._client.get(url, headers={"User-Agent": USER_AGENT})
        if response.status_code in (401, 403, 429):
            raise NoticeRefused(f"{url}: answered {response.status_code}; stopping the run")
        response.raise_for_status()
        return response


class NoticeStore:
    """Raw PDFs in a local directory and their provenance in an append-only JSONL file."""

    def __init__(self, directory: Path, provenance: Path) -> None:
        self._directory = directory
        self._provenance = provenance

    def collected(self) -> set[str]:
        if not self._provenance.is_file():
            return set()
        lines = self._provenance.read_text(encoding="utf-8").splitlines()
        return {json.loads(line)["url"] for line in lines if line}

    def path(self, ref: NoticeRef) -> Path:
        return self._directory / ref.name

    def save(self, ref: NoticeRef, content: bytes, fetched_on: date) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self.path(ref).write_bytes(content)
        self._provenance.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "url": ref.url, "title": ref.title, "notice_date": ref.notice_date.isoformat(),
            "fetched_on": fetched_on.isoformat(), "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }  # fmt: skip
        with self._provenance.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


class IndexNoticeCollector:
    """Fetch, one at a time, every candidate the store does not hold yet."""

    def __init__(self, source: IndexNoticeSource, store: NoticeStore, today: date) -> None:
        self._source = source
        self._store = store
        self._today = today

    async def run(self, candidates: Sequence[NoticeRef]) -> tuple[int, list[str]]:
        """(fetched, failed urls). A refusal propagates."""
        held = self._store.collected()
        fetched, failed = 0, []
        for ref in candidates:
            if ref.url in held:
                continue
            try:
                content = await self._source.pdf(ref.url)
            except NoticeRefused:
                raise
            except httpx.HTTPError:
                failed.append(ref.url)
                continue
            self._store.save(ref, content, self._today)
            fetched += 1
        return fetched, failed
