"""Fetch, extract and keep the text of filing attachments (PROFIT_PLAN §12.2, §8, EM-239).

`AttachmentTextStore` is append-only JSONL, one line per attachment URL asked about: the URL, when,
what came of it (`ok`, `image_only`, `not_pdf`, `not_found`, `error`), the extracted text, the
file's size and hash. A URL in the store is not fetched again, so a run resumes.
`AttachmentCollector` takes the URLs in priority order and fetches them one polite request at a
time; a refusal stops the run and three failures in a row halt it (that pattern is a block)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from emporos.core.clock import Clock
from emporos.research.filings.pdf_text import PdfExtractionError, PdfTextExtractor
from emporos.research.filings.polite import NotFound, PoliteGet

__all__ = [
    "DEFAULT_TEXT_DIR", "AttachmentCollector", "AttachmentHalted", "AttachmentReport",
    "AttachmentText", "AttachmentTextStore",
]  # fmt: skip

DEFAULT_TEXT_DIR = Path.home() / ".cache" / "emporos" / "filings" / "text"
MAX_CONSECUTIVE_FAILURES = 3
STATUSES = ("ok", "image_only", "not_pdf", "not_found", "error")


class AttachmentHalted(RuntimeError):
    """Consecutive failures that look like a block: the run stops."""


@dataclass(frozen=True)
class AttachmentText:
    url: str
    fetched_at: datetime
    status: str
    text: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status is one of {STATUSES}")

    def as_document(self) -> dict[str, object]:
        return {
            "url": self.url, "fetched_at": self.fetched_at.isoformat(), "status": self.status,
            "text": self.text, "size": self.size, "sha256": self.sha256,
        }  # fmt: skip


class AttachmentTextStore:
    def __init__(self, root: Path = DEFAULT_TEXT_DIR) -> None:
        self._file = root / "attachments.jsonl"

    def all(self) -> Iterator[AttachmentText]:
        if not self._file.exists():
            return
        with self._file.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    d = json.loads(line)
                    yield AttachmentText(
                        d["url"], datetime.fromisoformat(d["fetched_at"]), d["status"], d["text"],
                        d["size"], d["sha256"],
                    )  # fmt: skip

    def urls(self) -> set[str]:
        return {a.url for a in self.all()}

    def append(self, item: AttachmentText) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with self._file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item.as_document()) + "\n")


@dataclass
class AttachmentReport:
    fetched: int = 0
    skipped: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


class AttachmentCollector:
    def __init__(
        self, getter: PoliteGet, extractor: PdfTextExtractor, store: AttachmentTextStore,
        clock: Clock,
    ) -> None:  # fmt: skip
        self._getter = getter
        self._extractor = extractor
        self._store = store
        self._clock = clock

    async def run(
        self, urls: Iterable[str], limit: int | None, progress: Callable[[str], None]
    ) -> AttachmentReport:
        report = AttachmentReport()
        held = self._store.urls()
        streak = 0
        for url in urls:
            if url in held:
                report.skipped += 1
                continue
            if limit is not None and report.fetched >= limit:
                break
            try:
                item = await self._one(url)
            except (httpx.HTTPError, OSError) as error:
                streak += 1
                progress(f"{url}: failed ({error})")
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    raise AttachmentHalted(f"{streak} failures in a row; stopping") from error
                continue
            streak = 0
            self._store.append(item)
            held.add(url)
            report.fetched += 1
            report.by_status[item.status] = report.by_status.get(item.status, 0) + 1
            if report.fetched % 50 == 0:
                progress(f"{report.fetched} fetched: {report.by_status}")
        return report

    async def _one(self, url: str) -> AttachmentText:
        now = self._clock.now()
        try:
            body = await self._getter.get(url)
        except NotFound:
            return AttachmentText(url, now, "not_found", "", 0, "")
        try:
            extracted = self._extractor.extract(body)
        except PdfExtractionError as error:
            status = "not_pdf" if "%PDF" in str(error) else "error"
            return AttachmentText(url, now, status, "", len(body), "")
        status = "image_only" if extracted.image_only else "ok"
        return AttachmentText(url, now, status, extracted.text, extracted.size, extracted.sha256)
