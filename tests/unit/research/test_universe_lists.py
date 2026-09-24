"""EM-191 D1: the constituent-list reader, and the committed lists themselves."""

from __future__ import annotations

from pathlib import Path

import pytest

from emporos.research.universe_lists import ConstituentList, combined_symbols

HEADER = "Company Name,Industry,Symbol,Series,ISIN Code\n"
ROOT = Path(__file__).resolve().parents[3] / "config" / "universe" / "d1"


def parse(body: str, index: str = "X") -> ConstituentList:
    return ConstituentList.parse(index, HEADER + body)


class TestParse:
    def test_rows_become_constituents_with_the_masters_trading_symbol(self) -> None:
        (row,) = parse("ABB India Ltd.,Capital Goods,ABB,EQ,INE117A01022\n").rows

        assert (row.company, row.industry, row.isin) == (
            "ABB India Ltd.",
            "Capital Goods",
            "INE117A01022",
        )
        assert row.trading_symbol == "ABB-EQ"

    def test_a_byte_order_mark_and_blank_lines_are_tolerated(self) -> None:
        text = "﻿" + HEADER + "\nA Ltd.,Auto,AAA,EQ,INE000000001\n\n"
        assert len(ConstituentList.parse("X", text)) == 1

    def test_a_quoted_company_name_with_a_comma_survives(self) -> None:
        (row,) = parse('"Tata Steel, Ltd.",Metals,TATASTEEL,EQ,INE081A01020\n').rows
        assert row.company == "Tata Steel, Ltd."

    def test_another_header_is_refused(self) -> None:
        with pytest.raises(ValueError, match="header"):
            ConstituentList.parse("X", "Name,Symbol\nA,B\n")

    def test_a_non_equity_series_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not EQ"):
            parse("A Ltd.,Auto,AAA,BE,INE000000001\n")

    def test_a_repeated_symbol_is_refused(self) -> None:
        with pytest.raises(ValueError, match="twice"):
            parse("A,Auto,AAA,EQ,I1\nB,Auto,AAA,EQ,I2\n")

    def test_an_empty_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse("")


class TestCombined:
    def test_overlap_is_counted_once_and_order_is_kept(self) -> None:
        first = parse("A,Auto,AAA,EQ,I1\nB,Auto,BBB,EQ,I2\n", "one")
        second = parse("B,Auto,BBB,EQ,I2\nC,Auto,CCC,EQ,I3\n", "two")

        assert combined_symbols([first, second]) == ["AAA-EQ", "BBB-EQ", "CCC-EQ"]


class TestCommittedLists:
    def test_the_two_published_lists_load_at_their_published_sizes(self) -> None:
        n100 = ConstituentList.load("NIFTY 100", ROOT / "nifty100.csv")
        mid = ConstituentList.load("NIFTY Midcap 150", ROOT / "niftymidcap150.csv")

        assert (len(n100), len(mid)) == (100, 150)
        assert len(combined_symbols([n100, mid])) == 250  # the two indices do not overlap

    def test_the_provenance_file_names_both_lists(self) -> None:
        text = (ROOT / "SOURCE.yaml").read_text(encoding="utf-8")
        assert "nifty100.csv" in text and "niftymidcap150.csv" in text and "survivorship" in text
