"""Is a tier of attachment text extracted yet? (PROFIT_PLAN §12.2, EM-239).

A Dev run reads a frozen snapshot only after the MATERIAL attachments it will see have been read.
`ExtractionProgress.of` counts, over the filings published in [first, last] with an attachment in a
given priority tier and (by default) on a name that has an instrument id, how many have a stored
extraction record of ANY status. `ok`, `image_only`, `not_pdf`, `not_found` and `error` are all
final (the extractor does not retry them); only a filing with no record is pending. Done means
nothing is pending. It reads the raw filings and the text store directly, so it needs no rebuilt
event store."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from emporos.research.filings.attachments import AttachmentText
from emporos.research.filings.filing import Filing
from emporos.research.filings.priority import AttachmentPriority

__all__ = ["ExtractionProgress"]


@dataclass(frozen=True)
class ExtractionProgress:
    total: int
    pending: int
    by_status: Mapping[str, int]

    @property
    def done(self) -> bool:
        return self.total > 0 and self.pending == 0

    def detail(self) -> str:
        held = self.total - self.pending
        statuses = ", ".join(f"{k} {v}" for k, v in sorted(self.by_status.items())) or "none"
        return f"{held} of {self.total} read ({self.pending} pending); {statuses}"

    @staticmethod
    def of(
        filings: Iterable[Filing],
        texts: Mapping[str, AttachmentText],
        instrument_ids: Mapping[str, str],
        first: date,
        last: date,
        tier: int = 1,
        d1_only: bool = True,
    ) -> ExtractionProgress:
        total = pending = 0
        statuses: Counter[str] = Counter()
        seen: set[str] = set()
        for filing in filings:
            day = filing.published_at.date()
            if not (first <= day <= last) or not filing.attachment_url:
                continue
            if AttachmentPriority.tier(filing.category) != tier:
                continue
            if d1_only and not instrument_ids.get(filing.symbol):
                continue
            key = f"{filing.source}:{filing.source_id}"
            if key in seen:
                continue
            seen.add(key)
            total += 1
            record = texts.get(filing.attachment_url)
            if record is None:
                pending += 1
            else:
                statuses[record.status] += 1
        return ExtractionProgress(total, pending, dict(statuses))
