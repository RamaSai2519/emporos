"""EM-191 D5: results filings keep the exchange's own public time, collapse to the first filing of
each season, and land in an append-only ledger that resumes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from emporos.core.clock import IST
from emporos.research.results_filings import (
    FilingLedger,
    ResultsFiling,
    first_public_results,
    parse_filings,
)

NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)


def row(
    stamp: str, seq: str, desc: str = "Financial Result Updates", symbol: str = "RELIANCE"
) -> dict:  # type: ignore[type-arg]
    return {
        "an_dt": stamp, "desc": desc, "seq_id": seq, "symbol": symbol, "sm_isin": "INE002A01018",
        "attchmntText": " Reliance has submitted the financial results. ",
        "attchmntFile": f"https://example.invalid/{seq}.pdf",
    }  # fmt: skip


def filing(day: datetime, seq: str, symbol: str = "AAA") -> ResultsFiling:
    return ResultsFiling(symbol, "INE0", day, seq, "results", "")


class TestParse:
    def test_the_exchanges_time_is_read_as_ist(self) -> None:
        (f,) = parse_filings([row("22-Apr-2024 19:01:25", "1")], "RELIANCE")

        assert f.public_at == datetime(2024, 4, 22, 19, 1, 25, tzinfo=IST)
        assert f.public_at.utcoffset() == timedelta(hours=5, minutes=30)
        assert f.text == "Reliance has submitted the financial results."

    def test_other_subjects_are_ignored(self) -> None:
        payload = [row("22-Apr-2024 19:01:25", "1"), row("22-Apr-2024 18:00:00", "2", "Dividend")]
        assert [f.seq_id for f in parse_filings(payload, "RELIANCE")] == ["1"]

    def test_filings_come_back_oldest_first(self) -> None:
        payload = [row("22-Apr-2024 19:01:25", "2"), row("19-Jan-2024 18:13:53", "1")]
        assert [f.seq_id for f in parse_filings(payload, "RELIANCE")] == ["1", "2"]

    def test_a_row_without_a_usable_time_is_refused(self) -> None:
        with pytest.raises(ValueError, match="usable time"):
            parse_filings([row("not a time", "1")], "RELIANCE")

    def test_a_reply_that_is_not_a_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="expected a list"):
            parse_filings({"error": "x"}, "RELIANCE")


class TestFirstPublic:
    def test_a_revised_copy_a_day_later_is_not_a_new_season(self) -> None:
        a = datetime(2024, 1, 19, 18, 13, tzinfo=IST)
        firsts = first_public_results([filing(a + timedelta(days=1), "2"), filing(a, "1")])

        assert [f.seq_id for f in firsts] == ["1"]  # the earliest is when the market could know

    def test_the_next_quarter_is_a_new_season(self) -> None:
        a = datetime(2024, 1, 19, 18, 13, tzinfo=IST)
        firsts = first_public_results([filing(a, "1"), filing(a + timedelta(days=91), "2")])

        assert [f.seq_id for f in firsts] == ["1", "2"]

    def test_names_are_clustered_separately(self) -> None:
        a = datetime(2024, 1, 19, 18, 13, tzinfo=IST)
        firsts = first_public_results(
            [filing(a, "1", "AAA"), filing(a + timedelta(days=2), "2", "BBB")]
        )

        assert [f.symbol for f in firsts] == ["AAA", "BBB"]

    def test_a_long_run_of_amendments_cannot_chain_into_the_next_season(self) -> None:
        a = datetime(2024, 1, 1, tzinfo=IST)
        chain = [filing(a + timedelta(days=15 * n), str(n)) for n in range(4)]  # 0, 15, 30, 45

        assert [f.seq_id for f in first_public_results(chain)] == [
            "0",
            "2",
        ]  # measured from the start


class TestLedger:
    def make(self, tmp_path: Path) -> FilingLedger:
        return FilingLedger(tmp_path / "f.jsonl", tmp_path / "m.jsonl")

    def test_filings_round_trip_and_the_name_is_marked_collected(self, tmp_path: Path) -> None:
        ledger = self.make(tmp_path)
        f = filing(datetime(2024, 1, 19, 18, 13, tzinfo=IST), "1")

        assert ledger.record("AAA", [f], date(2016, 10, 3), date(2026, 9, 18), NOW) == 1

        assert ledger.load() == [f]
        assert ledger.collected_symbols() == {"AAA"}

    def test_a_filing_is_stored_once(self, tmp_path: Path) -> None:
        ledger = self.make(tmp_path)
        f = filing(datetime(2024, 1, 19, 18, 13, tzinfo=IST), "1")
        ledger.record("AAA", [f], date(2016, 10, 3), date(2026, 9, 18), NOW)

        assert ledger.record("AAA", [f], date(2016, 10, 3), date(2026, 9, 18), NOW) == 0
        assert len(ledger.load()) == 1

    def test_a_name_with_no_filings_is_still_collected(self, tmp_path: Path) -> None:
        ledger = self.make(tmp_path)
        ledger.record("NEW", [], date(2016, 10, 3), date(2026, 9, 18), NOW)

        assert ledger.collected_symbols() == {"NEW"}
        assert ledger.load() == []

    def test_an_empty_ledger_reads_empty(self, tmp_path: Path) -> None:
        assert self.make(tmp_path).load() == []
        assert self.make(tmp_path).collected_symbols() == set()


def test_a_filing_needs_a_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        ResultsFiling("A", "", datetime(2024, 1, 1), "1", "", "")
