"""A corporate announcement as a point-in-time record (PROFIT_PLAN §12.2, EM-239).

`published_at` is when the EXCHANGE disseminated the filing (NSE `exchdisstime`), the moment the
market could first read it. `submitted_at` is when the company filed it (NSE `an_dt`), a few
seconds to minutes earlier, and is kept but never used to time a decision. Both are timezone-aware
IST. `subject` is the feed's own text for the filing (NSE's `attchmntText`); the attachment's own
text is extracted later and lives in the event store, not here. Every filing carries the URL it was
fetched from and the day it was fetched (§8)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from emporos.core.clock import IST

__all__ = ["Filing", "parse_nse_filings"]

_STAMP = "%d-%b-%Y %H:%M:%S"


@dataclass(frozen=True)
class Filing:
    source: str  # "NSE" or "BSE"
    symbol: str  # the NSE trading symbol (BSE filings are mapped to it by ISIN)
    isin: str
    company: str
    source_id: str  # the exchange's own sequence id
    published_at: datetime  # exchange dissemination time, IST
    submitted_at: datetime | None  # the company's filing time, IST
    category: str
    subject: str
    attachment_url: str
    source_url: str
    fetched_on: date

    def __post_init__(self) -> None:
        if self.published_at.tzinfo is None:
            raise ValueError("a filing's publication time must carry a timezone")
        if not self.symbol or not self.source_id:
            raise ValueError("a filing needs a symbol and the exchange's own id")


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return datetime.strptime(value.strip(), _STAMP).replace(tzinfo=IST)


def parse_nse_filings(body: bytes, source_url: str, fetched_on: date) -> Sequence[Filing]:
    """The filings in one NSE `corporate-announcements` reply, as they came (not deduplicated)."""
    rows: list[dict[str, Any]] = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError("the announcements reply is not a list")
    filings: list[Filing] = []
    for row in rows:
        submitted = _stamp(row.get("an_dt"))
        published = _stamp(row.get("exchdisstime")) or submitted
        if published is None:
            raise ValueError(f"an announcement without a time: {row.get('seq_id')}")
        filings.append(
            Filing(
                "NSE",
                str(row["symbol"]).strip(),
                str(row.get("sm_isin") or "").strip(),
                str(row.get("sm_name") or "").strip(),
                str(row["seq_id"]).strip(),
                published,
                submitted,
                str(row.get("desc") or "").strip(),
                " ".join(str(row.get("attchmntText") or "").split()),
                str(row.get("attchmntFile") or "").strip(),
                source_url,
                fetched_on,
            )
        )
    return filings
