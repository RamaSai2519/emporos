"""Participant-wise F&O open interest and volume: what FIIs, DIIs, Pro and Client hold (EM-244).

NSE's clearing corporation publishes, every trading day after the close, two static files:
`fao_participant_oi_<DDMMYYYY>.csv` (contracts open at the day's end) and
`fao_participant_vol_<DDMMYYYY>.csv` (contracts traded), each with one row per participant class
(`Client`, `DII`, `FII`, `Pro`) and 14 columns of long and short contracts in index and stock
futures and options. They are published that evening, so a day's file is usable from the NEXT
open: `available_at` is 23:59 IST of the file's own date.

A file is not cash-market flow (FII buying of shares is not in it); it is the participant's
derivatives POSITIONING, which is what the index-futures long-short ratio and the options
positions people watch are made of."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time

from emporos.core.clock import IST
from emporos.research.cause_ledger.pages import PageSpec

__all__ = [
    "PARTICIPANT_URL", "PARTICIPANTS", "ParticipantRow", "available_at", "parse_participant_file",
    "participant_pages",
]  # fmt: skip

PARTICIPANT_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_{kind}_{day}.csv"
PARTICIPANTS = ("Client", "DII", "FII", "Pro")
COLUMNS = (
    "fut_index_long", "fut_index_short", "fut_stock_long", "fut_stock_short",
    "opt_index_call_long", "opt_index_put_long", "opt_index_call_short", "opt_index_put_short",
    "opt_stock_call_long", "opt_stock_put_long", "opt_stock_call_short", "opt_stock_put_short",
    "total_long", "total_short",
)  # fmt: skip


@dataclass(frozen=True)
class ParticipantRow:
    day: date
    kind: str  # "oi" (open at the close) or "vol" (traded that day)
    participant: str
    fut_index_long: int
    fut_index_short: int
    fut_stock_long: int
    fut_stock_short: int
    opt_index_call_long: int
    opt_index_put_long: int
    opt_index_call_short: int
    opt_index_put_short: int
    opt_stock_call_long: int
    opt_stock_put_long: int
    opt_stock_call_short: int
    opt_stock_put_short: int
    total_long: int
    total_short: int

    @property
    def net_index_futures(self) -> int:
        return self.fut_index_long - self.fut_index_short

    @property
    def net_index_calls(self) -> int:
        return self.opt_index_call_long - self.opt_index_call_short

    @property
    def net_index_puts(self) -> int:
        return self.opt_index_put_long - self.opt_index_put_short

    @property
    def index_futures_long_share(self) -> float | None:
        total = self.fut_index_long + self.fut_index_short
        return self.fut_index_long / total if total else None


def available_at(day: date) -> datetime:
    """The files are published in the evening: usable from the next open, so from 23:59 IST."""
    return datetime.combine(day, time(23, 59), tzinfo=IST)


def participant_pages(days: list[date]) -> list[PageSpec]:
    """One OI and one volume file per trading day asked for; a 404 is a holiday, not an error."""
    pages: list[PageSpec] = []
    for day in days:
        tag = day.strftime("%d%m%Y")
        for kind in ("oi", "vol"):
            pages.append(
                PageSpec(
                    f"nse-participant-{kind}",
                    f"participant/{kind}/{day.isoformat()}.csv",
                    PARTICIPANT_URL.format(kind=kind, day=tag),
                    absent_is_data=True,
                )  # fmt: skip
            )
    return pages


def _number(text: str) -> int:
    return int(float(text.strip().replace(",", "") or 0))


def parse_participant_file(text: str, day: date, kind: str) -> list[ParticipantRow]:
    """The participant rows of one file (an empty text, a holiday, gives none). The header row is
    the one whose first cell is `Client Type`; the title line above it is skipped."""
    rows = list(csv.reader(io.StringIO(text.replace("﻿", ""))))
    header = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Client Type"), None)
    if header is None:
        return []
    out: list[ParticipantRow] = []
    for r in rows[header + 1 :]:
        if not r or r[0].strip() not in PARTICIPANTS:
            continue  # the TOTAL row and blank lines
        numbers = [_number(c) for c in r[1 : 1 + len(COLUMNS)] if c.strip() != ""]
        if len(numbers) != len(COLUMNS):
            raise ValueError(f"{day} {r[0]}: {len(numbers)} numbers, expected {len(COLUMNS)}")
        out.append(ParticipantRow(day, kind, r[0].strip(), *numbers))
    return out
