"""EM-244: insider (PIT) and SAST disclosures: parsing, dissemination-time availability, monthly
pages, and the Parquet ledger."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from emporos.core.clock import IST
from emporos.research.cause_ledger.insider import monthly_pages, parse_pit, parse_sast
from emporos.research.cause_ledger.insider_store import InsiderLedger

PIT = {
    "acqMode": "Market Sale", "acqName": "R Sridhar", "acqfromDt": "29-Feb-2024",
    "acqtoDt": "29-Feb-2024", "afterAcqSharesNo": "2750", "befAcqSharesNo": "4000",
    "company": "Landmark Cars Limited", "date": "07-Mar-2024 19:58", "personCategory": "Director",
    "secAcq": "1250", "secType": "Equity Shares", "secVal": "898253", "symbol": "LANDMARK",
    "tdpTransactionType": "Sell", "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/x.xml",
}  # fmt: skip
PLEDGE = {**PIT, "acqMode": "Pledge Creation", "tdpTransactionType": "Pledge", "symbol": "GENSOL",
          "date": "31-Mar-2024 12:00", "acqName": "Anmol Singh Jaggi"}  # fmt: skip
SAST = {
    "acqSaleType": "Both", "acquirerDate": "02-MAR-2024 to 02-MAR-2024",
    "acquirerName": "AGAM GUPTA",
    "acquisitionMode": "Open Market", "attachement": "https://x/a.zip", "company": "Share India",
    "noOfShareAcq": "106600", "noOfShareAft": "738170", "noOfShareSale": None, "promoterType": "Y",
    "regType": "Reg29(2)", "symbol": "SHAREINDIA", "timestamp": "07-Mar-2024 20:46",
    "totAcqShare": "0.3", "totAftShare": "2.06",
}  # fmt: skip


def body(*records: dict[str, object]) -> bytes:
    return json.dumps({"acqNameList": [], "data": list(records)}).encode()


def test_a_pit_row_is_available_when_the_exchange_disseminated_it_not_when_the_trade_was_done() -> (
    None
):
    (trade,) = parse_pit(body(PIT))

    assert trade.trade_from == date(2024, 2, 29)
    assert trade.published_at == datetime(2024, 3, 7, 19, 58, tzinfo=IST)
    assert trade.published_at.date() > trade.trade_from  # filed days after the trade
    assert (trade.person, trade.transaction, trade.quantity, trade.value) == (
        "R Sridhar", "Sell", 1250, 898253.0,
    )  # fmt: skip
    assert (trade.holding_before, trade.holding_after) == (4000, 2750)


def test_pledges_are_kept_with_their_own_transaction_type() -> None:
    (row,) = parse_pit(body(PLEDGE))

    assert row.transaction == "Pledge" and row.mode == "Pledge Creation"


def test_a_row_without_a_symbol_or_a_quantity_is_dropped_not_guessed() -> None:
    broken = {**PIT, "secAcq": "n/a"}
    nameless = {k: v for k, v in PIT.items() if k != "symbol"}

    assert parse_pit(body(broken, nameless)) == []


def test_a_sast_row_is_timed_by_its_own_dissemination_timestamp() -> None:
    (row,) = parse_sast(body(SAST))

    assert row.published_at == datetime(2024, 3, 7, 20, 46, tzinfo=IST)
    assert row.is_promoter and row.regulation == "Reg29(2)"
    assert (row.shares_acquired, row.shares_after, row.shares_sold) == (106600, 738170, None)
    assert row.percent_after == 2.06


def test_one_pit_and_one_sast_page_per_month_of_the_span() -> None:
    pages = monthly_pages(date(2024, 1, 1), date(2024, 3, 18))

    assert [p.name for p in pages] == [
        "pit/2024-01.json", "sast/2024-01.json", "pit/2024-02.json", "sast/2024-02.json",
        "pit/2024-03.json", "sast/2024-03.json",
    ]  # fmt: skip
    assert "from_date=01-02-2024&to_date=29-02-2024" in pages[2].url  # 2024 is a leap year
    assert all(p.url.startswith("https://www.nseindia.com/api/") for p in pages)


def test_the_ledger_writes_sorted_deduplicated_rows_inside_the_span(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "pit").mkdir(parents=True)
    (raw / "sast").mkdir()
    (raw / "pit" / "2024-03.json").write_bytes(body(PLEDGE, PIT))
    (raw / "pit" / "2024-04.json").write_bytes(body(PIT))  # the same filing in an overlapping month
    (raw / "sast" / "2024-03.json").write_bytes(body(SAST))
    ledger = InsiderLedger(raw, tmp_path / "out")

    summary = ledger.build(date(2024, 3, 1), date(2024, 3, 31))

    assert (summary.pit_rows, summary.sast_rows) == (2, 1)
    assert [t.symbol for t in ledger.read_pit()] == ["LANDMARK", "GENSOL"]  # by dissemination time
    assert ledger.read_sast()[0].published_at == datetime(2024, 3, 7, 20, 46, tzinfo=IST)
    assert ledger.build(date(2024, 3, 20), date(2024, 3, 31)).pit_rows == 1  # only the 31st
