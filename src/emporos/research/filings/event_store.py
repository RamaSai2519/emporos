"""The Track L event store: filings as `MarketEvent`s in versioned local Parquet (EM-239, §12.2).

`EventBuilder` turns collected filings (plus the extracted attachment text) into rows: one event per
filing, ordered and identified `NSE:<the exchange's own id>`. There is no second exchange to
deduplicate against (BSE refuses automated access with an honest user agent, PROFIT_PLAN §8: we do
not get around it), so `dedupe_across_exchanges` is the rule the day a BSE source exists: the same
company, a near-identical subject within `WINDOW`, keep the earlier.

`ParquetEventStore` is the reader (`EventStore`): `<root>/v1/month=YYYY-MM.parquet` files (IST month
of the publication time), an id index, and a manifest carrying the schema version, so a reader
refuses a layout it does not know. It reads only the months a query touches. A row keeps more
than `MarketEvent` does (the company's own filing time, the ISIN, the attachment link, whether its
text is from the PDF, the source URL and fetch date) for audit and the report.

`text` is the attachment's PDF text when it was extracted and had text, else the feed's own subject
line, so it is never empty; `text_status` says which it is (`ok`, `image_only`, `not_pdf`,
`not_found`, `error`; `pending` when it has an attachment not fetched yet; `none` when the
filing has no attachment)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from emporos.core.clock import IST
from emporos.eventtrader.events import FILING, MarketEvent
from emporos.research.filings.attachments import AttachmentText
from emporos.research.filings.filing import Filing

__all__ = [
    "DEFAULT_EVENT_DIR", "SCHEMA_VERSION", "EventBuilder", "EventRow", "ParquetEventStore",
    "dedupe_across_exchanges",
]  # fmt: skip

DEFAULT_EVENT_DIR = Path.home() / ".cache" / "emporos" / "events"
SCHEMA_VERSION = 1
WINDOW = timedelta(minutes=10)
MAX_TEXT_CHARS = 200_000

_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()), ("source", pa.string()), ("symbol", pa.string()),
        ("instrument_id", pa.string()), ("isin", pa.string()), ("company", pa.string()),
        ("published_at", pa.timestamp("us", tz="UTC")),
        ("submitted_at", pa.timestamp("us", tz="UTC")), ("category", pa.string()),
        ("subject", pa.string()), ("text", pa.string()), ("text_status", pa.string()),
        ("attachment_url", pa.string()), ("source_url", pa.string()), ("fetched_on", pa.date32()),
    ]
)  # fmt: skip


@dataclass(frozen=True)
class EventRow:
    event_id: str
    source: str
    symbol: str
    instrument_id: str
    isin: str
    company: str
    published_at: datetime  # UTC
    submitted_at: datetime | None
    category: str
    subject: str
    text: str
    text_status: str
    attachment_url: str
    source_url: str
    fetched_on: date

    def as_event(self) -> MarketEvent:
        return MarketEvent(
            self.event_id, self.instrument_id, self.symbol, self.published_at, self.published_at,
            FILING, self.category, self.subject, self.text,
        )  # fmt: skip


def dedupe_across_exchanges(rows: Iterable[EventRow]) -> list[EventRow]:
    """Keep the earlier of two rows from DIFFERENT exchanges for the same company (ISIN) with the
    same normalised subject within `WINDOW`; rows from one exchange are never merged."""
    ordered = sorted(rows, key=lambda r: (r.published_at, r.event_id))
    kept: list[EventRow] = []
    for row in ordered:
        key = " ".join(row.subject.lower().split())
        twin = next(
            (
                k for k in reversed(kept)
                if row.published_at - k.published_at <= WINDOW
                and k.source != row.source and k.isin == row.isin
                and " ".join(k.subject.lower().split()) == key
            ),
            None,
        )  # fmt: skip
        if twin is None:
            kept.append(row)
    return kept


class EventBuilder:
    """Filings and attachment texts to rows. Pure: no reads, no clock."""

    def __init__(self, instrument_ids: Mapping[str, str]) -> None:
        self._ids = dict(instrument_ids)  # trading symbol -> "NSE:<token>"

    def build(
        self, filings: Iterable[Filing], texts: Mapping[str, AttachmentText]
    ) -> list[EventRow]:
        rows: dict[str, EventRow] = {}
        for filing in filings:
            event_id = f"{filing.source}:{filing.source_id}"
            if event_id in rows:
                continue  # the same filing seen in two windows or two runs
            attachment = texts.get(filing.attachment_url) if filing.attachment_url else None
            usable = attachment is not None and attachment.status == "ok" and attachment.text
            status = attachment.status if attachment is not None else self._unread(filing)
            rows[event_id] = EventRow(
                event_id, filing.source, filing.symbol, self._ids.get(filing.symbol, ""),
                filing.isin, filing.company, filing.published_at.astimezone(UTC),
                filing.submitted_at.astimezone(UTC) if filing.submitted_at else None,
                filing.category, filing.subject,
                (attachment.text[:MAX_TEXT_CHARS] if usable and attachment else filing.subject),
                status, filing.attachment_url, filing.source_url, filing.fetched_on,
            )  # fmt: skip
        return sorted(rows.values(), key=lambda r: (r.published_at, r.event_id))

    @staticmethod
    def _unread(filing: Filing) -> str:
        return "pending" if filing.attachment_url else "none"


def _month(moment: datetime) -> str:
    local = moment.astimezone(IST)
    return f"{local.year:04d}-{local.month:02d}"


def _months_between(start: datetime, end: datetime) -> list[str]:
    a, b = start.astimezone(IST), end.astimezone(IST)
    out, year, month = [], a.year, a.month
    while (year, month) <= (b.year, b.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


class ParquetEventStore:
    def __init__(self, root: Path = DEFAULT_EVENT_DIR) -> None:
        self._dir = root / f"v{SCHEMA_VERSION}"
        manifest = self._dir / "manifest.json"
        if manifest.exists():
            found = json.loads(manifest.read_text(encoding="utf-8")).get("schema_version")
            if found != SCHEMA_VERSION:
                raise ValueError(f"event store schema {found!r} is not the one this reader knows")

    @property
    def directory(self) -> Path:
        return self._dir

    # -- writing --------------------------------------------------------------------------------

    def write(
        self, rows: Sequence[EventRow], built_at: datetime, notes: Mapping[str, object]
    ) -> int:
        """Replace the store with `rows` (a pure function of what was collected): month files, the
        id index and the manifest, each written atomically. The number of months written."""
        by_month: dict[str, list[EventRow]] = {}
        for row in rows:
            by_month.setdefault(_month(row.published_at), []).append(row)
        self._dir.mkdir(parents=True, exist_ok=True)
        for stale in self._dir.glob("month=*.parquet"):
            stale.unlink()
        for month, part in sorted(by_month.items()):
            self._atomic(self._dir / f"month={month}.parquet", self._table(part))
        index = pa.table(
            {"event_id": [r.event_id for r in rows],
             "month": [_month(r.published_at) for r in rows]}
        )  # fmt: skip
        self._atomic(self._dir / "index.parquet", index)
        manifest = {
            "schema_version": SCHEMA_VERSION, "built_at": built_at.isoformat(), "events": len(rows),
            "months": sorted(by_month), **notes,
        }  # fmt: skip
        temporary = self._dir / "manifest.tmp"
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._dir / "manifest.json")
        return len(by_month)

    @staticmethod
    def _table(rows: Sequence[EventRow]) -> pa.Table:
        def column(name: str) -> list[object]:
            return [getattr(r, name) for r in rows]

        return pa.Table.from_pydict({f.name: column(f.name) for f in _SCHEMA}, schema=_SCHEMA)

    @staticmethod
    def _atomic(path: Path, table: pa.Table) -> None:
        temporary = path.with_suffix(".tmp")
        pq.write_table(table, temporary, compression="zstd")
        os.replace(temporary, path)

    # -- reading (EventStore) -------------------------------------------------------------------

    def rows_between(
        self, start: datetime, end: datetime, symbol: str | None = None
    ) -> list[EventRow]:
        """Rows with `published_at` in [start, end), sorted by (published_at, event_id)."""
        out: list[EventRow] = []
        for month in _months_between(start, end - timedelta(microseconds=1)):
            path = self._dir / f"month={month}.parquet"
            if not path.exists():
                continue
            table = pq.read_table(path, schema=_SCHEMA)
            mask = pc.and_(
                pc.greater_equal(
                    table["published_at"], pa.scalar(start, pa.timestamp("us", "UTC"))
                ),
                pc.less(table["published_at"], pa.scalar(end, pa.timestamp("us", "UTC"))),
            )
            if symbol is not None:
                mask = pc.and_(mask, pc.equal(table["symbol"], symbol))
            out.extend(_rows(table.filter(mask)))
        return sorted(out, key=lambda r: (r.published_at, r.event_id))

    def events_between(self, start: datetime, end: datetime) -> Sequence[MarketEvent]:
        return [r.as_event() for r in self.rows_between(start, end)]

    def for_symbol(self, symbol: str, start: datetime, end: datetime) -> Sequence[MarketEvent]:
        return [r.as_event() for r in self.rows_between(start, end, symbol)]

    def by_id(self, event_id: str) -> MarketEvent | None:
        index_file = self._dir / "index.parquet"
        if not index_file.exists():
            return None
        index = pq.read_table(index_file)
        hit = index.filter(pc.equal(index["event_id"], event_id))
        if hit.num_rows == 0:
            return None
        table = pq.read_table(
            self._dir / f"month={hit['month'][0].as_py()}.parquet", schema=_SCHEMA
        )
        found = _rows(table.filter(pc.equal(table["event_id"], event_id)))
        return found[0].as_event() if found else None


def _rows(table: pa.Table) -> list[EventRow]:
    data = table.to_pydict()
    return [EventRow(*(data[f.name][i] for f in _SCHEMA)) for i in range(table.num_rows)]
