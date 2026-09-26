"""EM-244: F&O expiry days read from the archive: contracts listed per day, stock and index events,
monthly versus weekly, and availability from the start of the expiry's own day."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from emporos.options.chain import OptionRight
from emporos.research.cause_ledger.expiries import ExpirySource, default_index_source
from emporos.research.fo_archive_rows import ArchiveFormat, IndexContractRow, InstrumentKind
from emporos.research.fo_archive_store import FoDayStore

DAY = date(2024, 3, 1)
CHECKED = date(2026, 9, 26)
URL = "https://example.org/fo"


def row(symbol: str, kind: InstrumentKind, expiry: date) -> IndexContractRow:
    right = OptionRight.CALL if kind is InstrumentKind.OPTION else None
    strike = Decimal(100) if right else None
    price = Decimal(1)
    return IndexContractRow(
        DAY, symbol, kind, expiry, strike, right, price, price, price, price, price,
        1, price, 1, 0, None, None, ArchiveFormat.LEGACY,
    )  # fmt: skip


def store(tmp_path: Path, rows: list[IndexContractRow]) -> FoDayStore:
    archive = FoDayStore(tmp_path)
    archive.write(DAY, rows)
    return archive


def test_contracts_lists_distinct_symbol_kind_expiry(tmp_path: Path) -> None:
    expiry = date(2024, 3, 28)
    archive = store(tmp_path, [row("TCS", InstrumentKind.FUTURE, expiry),
                               row("TCS", InstrumentKind.OPTION, expiry),
                               row("TCS", InstrumentKind.OPTION, expiry)])  # fmt: skip
    assert archive.contracts(DAY) == {("TCS", "FUT", expiry), ("TCS", "OPT", expiry)}


def test_stock_archive_gives_one_event_per_expiry_from_the_start_of_its_day(tmp_path: Path) -> None:
    march, april = date(2024, 3, 28), date(2024, 4, 25)
    archive = store(tmp_path, [row("TCS", InstrumentKind.FUTURE, march),
                               row("INFY", InstrumentKind.FUTURE, march),
                               row("INFY", InstrumentKind.FUTURE, april)])  # fmt: skip
    events = ExpirySource(archive, URL, CHECKED, "stock").events()
    assert [(e.kind, e.event_date) for e in events] == [
        ("fno_expiry_stock", march),
        ("fno_expiry_stock", april),
    ]
    assert events[0].available_at.date() == march
    assert events[0].available_at.hour == 0


def test_index_expiry_is_monthly_when_a_future_is_listed_else_weekly(tmp_path: Path) -> None:
    monthly, weekly = date(2024, 3, 28), date(2024, 3, 7)
    archive = store(tmp_path, [row("NIFTY", InstrumentKind.FUTURE, monthly),
                               row("NIFTY", InstrumentKind.OPTION, monthly),
                               row("NIFTY", InstrumentKind.OPTION, weekly),
                               row("TCS", InstrumentKind.FUTURE, monthly)])  # fmt: skip
    events = ExpirySource(archive, URL, CHECKED, "index", ("NIFTY",)).events()
    assert [(e.kind, e.event_date) for e in events] == [
        ("fno_expiry_nifty_weekly", weekly),
        ("fno_expiry_nifty_monthly", monthly),
    ]


def test_default_index_source_reads_the_given_root(tmp_path: Path) -> None:
    store(tmp_path, [row("BANKNIFTY", InstrumentKind.FUTURE, date(2024, 3, 27))])
    events = default_index_source(tmp_path, CHECKED).events()
    assert [e.kind for e in events] == ["fno_expiry_banknifty_monthly"]
