"""EM-246: the fixed list of stock-option underlyings and the scrip master read for options."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from emporos.cli.option_master import OptionSegmentFilter, ScripMasterContracts
from emporos.options.chain import OptionRight
from emporos.quotes.underlyings import DEFAULT_UNDERLYINGS, INDEX_RULES, index_and_stock_rules
from emporos.research.fo_archive_rows import ArchiveFormat, IndexContractRow, InstrumentKind
from emporos.research.option_universe import top_option_underlyings

D = Decimal


def row(symbol: str, kind: InstrumentKind, lots: int, day: date) -> IndexContractRow:
    right = OptionRight.CALL if kind is InstrumentKind.OPTION else None
    strike = D(100) if right else None
    return IndexContractRow(
        day, symbol, kind, date(2026, 3, 30), strike, right, D(1), D(1), D(1), D(1), D(1), lots,
        D(1), 1, 0, None, 100, ArchiveFormat.UDIFF,
    )  # fmt: skip


class Days:
    def __init__(self, rows: list[IndexContractRow]) -> None:
        self._rows = rows

    def days(self) -> list[date]:
        return sorted({r.day for r in self._rows})

    def read(self, day: date, symbol: str | None = None) -> list[IndexContractRow]:
        return [r for r in self._rows if r.day == day]


class TestTopOptionUnderlyings:
    def test_the_most_traded_names_in_the_symbol_table_and_only_option_rows(self) -> None:
        day, older = date(2026, 3, 18), date(2026, 3, 17)
        store = Days(
            [
                row("TCS", InstrumentKind.OPTION, 900, day),
                row("TCS", InstrumentKind.OPTION, 100, day),
                row("INFY", InstrumentKind.OPTION, 500, day),
                row("SBIN", InstrumentKind.FUTURE, 9999, day),  # a future is not an option
                row("NOTD1", InstrumentKind.OPTION, 8000, day),  # no cash quote to centre on
                row("ZZZ", InstrumentKind.OPTION, 7000, older),  # an older session
            ]
        )
        ids = {"TCS": "NSE:11536", "INFY": "NSE:1594", "SBIN": "NSE:3045", "ZZZ": "NSE:9"}

        used, top = top_option_underlyings(store, ids, count=5)

        assert used == (day,)
        assert [(t.symbol, t.lots) for t in top] == [("TCS", 1000), ("INFY", 500)]
        assert top[0].spot_id == "NSE:11536"

    def test_ties_break_by_symbol_and_the_count_caps_the_list(self) -> None:
        day = date(2026, 3, 18)
        store = Days([row(s, InstrumentKind.OPTION, 10, day) for s in ("B", "A", "C")])

        _, top = top_option_underlyings(store, {"A": "1", "B": "2", "C": "3"}, count=2)

        assert [t.symbol for t in top] == ["A", "B"]


class TestUnderlyingsFile:
    def test_the_committed_list_has_twenty_distinct_names_and_the_index_rules_come_first(
        self,
    ) -> None:
        rules = index_and_stock_rules(DEFAULT_UNDERLYINGS)

        assert rules[: len(INDEX_RULES)] == INDEX_RULES
        stocks = rules[len(INDEX_RULES) :]
        assert len(stocks) == 20 == len({r.underlying for r in stocks})
        assert {r.underlying for r in stocks} >= {"M&M", "TCS"}
        assert all(r.expiries == 1 and r.each_side == 2 for r in stocks)
        assert [r.underlying for r in INDEX_RULES] == ["NIFTY", "BANKNIFTY"]
        assert all(r.expiries == 2 and r.each_side == 5 for r in INDEX_RULES)

    def test_a_broken_file_is_an_error_not_an_empty_list(self, tmp_path: Path) -> None:
        bad = tmp_path / "x.yaml"
        bad.write_text("nothing: here\n")

        with pytest.raises(KeyError):
            index_and_stock_rules(bad)


MASTER = [
    {"token": "1", "symbol": "NIFTY29SEP2624500CE", "name": "NIFTY", "expiry": "29SEP2026",
     "strike": "2450000.0", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    {"token": "2", "symbol": "TCS29SEP263000PE", "name": "TCS", "expiry": "29SEP2026",
     "strike": "300000.0", "lotsize": "175", "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
    {"token": "3", "symbol": "TCS29SEP26FUT", "name": "TCS", "expiry": "29SEP2026",
     "strike": "-1.0", "lotsize": "175", "instrumenttype": "FUTSTK", "exch_seg": "NFO"},
    {"token": "4", "symbol": "WIPRO29SEP26400CE", "name": "WIPRO", "expiry": "29SEP2026",
     "strike": "40000.0", "lotsize": "1500", "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
    {"token": "5", "symbol": "TCS-EQ", "name": "TCS", "expiry": "", "strike": "-1.0",
     "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE"},
]  # fmt: skip


class TestScripMaster:
    def test_only_option_rows_of_the_underlyings_asked_for_pass(self) -> None:
        keep = OptionSegmentFilter(["NIFTY", "TCS"])

        assert [r["token"] for r in MASTER if keep.accepts(r)] == ["1", "2"]

    async def test_the_master_is_downloaded_into_a_contract_book(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(MASTER))
        )

        book = await ScripMasterContracts(["NIFTY", "TCS"], transport=transport).load()

        assert len(book) == 2
        assert book.get("TCS", date(2026, 9, 29), D(3000), "PE") is not None
        assert book.strikes("NIFTY", date(2026, 9, 29)) == [D(24500)]
