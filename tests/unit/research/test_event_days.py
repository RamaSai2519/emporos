"""EM-191 L2: which session reacts to a results filing, and the per-instrument gate built on it."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.research.test_shock_reversal import FRIDAY, MONDAY, session

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.results_filings import ResultsFiling
from emporos.research.scans.base import IntradayScan, ScanExecution
from emporos.research.scans.earnings_gap import earnings_gap_scan
from emporos.research.scans.event_days import (
    EventReactionScan,
    InstrumentSymbols,
    SetOfDays,
    reaction_days,
    results_by_symbol,
)
from emporos.research.scans.raw_gap import GapDirection, RawGapParameters, RawGapRules
from emporos.research.scans.regime_gate import DayGatedRules

THURSDAY = FRIDAY - timedelta(days=1)
SUNDAY = FRIDAY + timedelta(days=2)
SESSIONS = [THURSDAY, FRIDAY, MONDAY, MONDAY + timedelta(days=1)]


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


class TestReactionDay:
    def test_after_the_close_the_next_session_reacts(self) -> None:
        assert reaction_days([at(FRIDAY, 16, 30)], SESSIONS).days == {MONDAY}

    def test_before_the_open_the_same_session_reacts(self) -> None:
        assert reaction_days([at(FRIDAY, 8, 30)], SESSIONS).days == {FRIDAY}

    def test_the_open_itself_is_in_session(self) -> None:
        r = reaction_days([at(FRIDAY, 9, 15)], SESSIONS)

        assert r.days == frozenset() and r.in_session == 1

    def test_during_the_session_nothing_reacts_and_it_is_counted(self) -> None:
        r = reaction_days([at(FRIDAY, 12, 0)], SESSIONS)

        assert (r.days, r.in_session, r.used) == (frozenset(), 1, 0)

    def test_the_close_itself_counts_as_after_hours(self) -> None:
        assert reaction_days([at(FRIDAY, 15, 30)], SESSIONS).days == {MONDAY}

    def test_a_weekend_filing_reacts_on_monday_whatever_the_time(self) -> None:
        assert reaction_days([at(SUNDAY, 8, 0), at(SUNDAY, 20, 0)], SESSIONS).days == {MONDAY}

    def test_two_events_in_one_reaction_session_are_one_day(self) -> None:
        r = reaction_days([at(FRIDAY, 17, 0), at(SUNDAY, 10, 0)], SESSIONS)

        assert (r.days, r.used) == ({MONDAY}, 1)

    def test_a_missing_reaction_session_is_not_replaced_by_a_later_one(self) -> None:
        sessions = [FRIDAY, MONDAY + timedelta(days=7)]  # the week after has a hole

        r = reaction_days([at(FRIDAY, 17, 0)], sessions)

        assert (r.days, r.no_session) == (frozenset(), 1)

    def test_an_event_outside_the_bars_is_not_this_scans_event(self) -> None:
        r = reaction_days(
            [at(date(2019, 1, 1), 10, 0), at(MONDAY + timedelta(days=30), 10, 0)], SESSIONS
        )

        assert (r.days, r.in_session, r.no_session) == (frozenset(), 0, 0)

    def test_no_sessions_means_no_days(self) -> None:
        assert reaction_days([at(FRIDAY, 17, 0)], []).days == frozenset()


def filing(symbol: str, when: datetime, seq: str = "1") -> ResultsFiling:
    return ResultsFiling(symbol, "INE", when, seq, "results", "a.zip", "Financial Result Updates")


class TestEventsBySymbol:
    def test_a_cluster_of_refilings_is_one_event_at_its_first_time(self) -> None:
        first = at(FRIDAY, 17, 0)
        filings = [filing("AAA", first, "1"), filing("AAA", first + timedelta(days=2), "2")]

        assert results_by_symbol(filings) == {"AAA": [first]}


class TestSymbols:
    def test_a_table_maps_candle_ids_to_symbols(self, tmp_path: Path) -> None:
        path = tmp_path / "t.csv"
        path.write_text("Symbol,Token\nAAA,11\nBBB,22\n")

        symbols = InstrumentSymbols.load(path)

        assert symbols.symbol("NSE:22") == "BBB"

    def test_an_unknown_id_is_refused_not_skipped(self) -> None:
        with pytest.raises(ValueError, match="NSE:9"):
            InstrumentSymbols({}).symbol("NSE:9")

    def test_the_committed_table_covers_the_audit_names(self) -> None:
        import json

        symbols = InstrumentSymbols.load(Path("config/universe/d1/tokens.csv"))
        audit = json.loads(Path("docs/data/history-quality.json").read_text())

        for span in audit["spans"]:
            assert symbols.symbol(span["instrument"])


def bars_two_sessions(second_open: str) -> list[Candle]:
    return session(FRIDAY, open_="100", at_hour="100", close="100") + session(
        MONDAY, open_=second_open, at_hour=second_open, close="99"
    )


def gap_scan(direction: GapDirection = GapDirection.FADE):  # type: ignore[no-untyped-def]
    execution = ScanExecution(Decimal(25_000))
    parameters = RawGapParameters(Decimal(1), direction, 1)

    def inner(gate: SetOfDays) -> IntradayScan:
        return IntradayScan(
            "gap_on_reaction_days",
            lambda: DayGatedRules(RawGapRules(parameters, execution.no_new_entries_after), gate),
            execution,
        )

    return inner


SYMBOLS = InstrumentSymbols({"NSE:1": "AAA", "NSE:2": "BBB"})


class TestEventReactionScan:
    def test_a_gap_is_traded_only_on_the_names_own_reaction_day(self) -> None:
        events = {"AAA": [at(FRIDAY, 17, 0)]}  # AAA reacts Monday; BBB has no event
        scan = EventReactionScan("x", gap_scan(), events, SYMBOLS)
        bars = bars_two_sessions("102")  # +2% gap Monday

        assert [t.day for t in scan.scan("NSE:1", bars)] == [MONDAY]
        assert scan.scan("NSE:2", bars) == []

    def test_a_gap_on_a_day_with_no_event_is_not_traded(self) -> None:
        events = {"AAA": [at(THURSDAY, 17, 0)]}  # reacts Friday, and Friday has no gap
        scan = EventReactionScan("x", gap_scan(), events, SYMBOLS)

        assert scan.scan("NSE:1", bars_two_sessions("102")) == []

    def test_the_outcome_reports_reaction_sessions_per_year_and_skips(self) -> None:
        events = {"AAA": [at(FRIDAY, 17, 0), at(MONDAY, 11, 0)]}
        scan = EventReactionScan("x", gap_scan(), events, SYMBOLS)

        scan.scan("NSE:1", bars_two_sessions("102"))

        outcome = scan.outcome
        assert dict(outcome.by_year) == {MONDAY.year: 1}
        assert (outcome.in_session, outcome.no_session) == (1, 0)


class TestEarningsGapScan:
    def test_the_shipped_scan_fades_a_reaction_day_gap_to_the_close(self) -> None:
        events = {"AAA": [at(FRIDAY, 17, 0)]}
        parameters = RawGapParameters(Decimal(1), GapDirection.FADE, 1)
        scan = earnings_gap_scan(parameters, ScanExecution(Decimal(25_000)), events, SYMBOLS)

        (trade,) = scan.scan("NSE:1", bars_two_sessions("102"))

        assert (trade.side, trade.day) == (OrderSide.SELL, MONDAY)
