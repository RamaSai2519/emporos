"""EM-244: calendar events and their availability times (each rule pinned), the FOMC parsers, the
manual rows and the builder's span."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from emporos.core.clock import IST
from emporos.research.cause_ledger.events import (
    CalendarEvent,
    IstRelease,
    KnownAhead,
    YamlCalendar,
    ZonedRelease,
)
from emporos.research.cause_ledger.fomc import parse_current_page, parse_historical_page
from emporos.research.cause_ledger.sources import CalendarBuilder, FedSource, ManualSource

SRC = "https://example.org/x"
CHECKED = date(2026, 9, 26)


def ist(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=IST)


class TestAvailability:
    def test_a_2pm_new_york_statement_is_2330_ist_in_us_daylight_time(self) -> None:
        rule = ZonedRelease(time(14, 0))
        assert rule.available_at(date(2024, 6, 12)) == ist(2024, 6, 12, 23, 30)

    def test_it_is_0030_ist_the_next_day_in_us_standard_time(self) -> None:
        rule = ZonedRelease(time(14, 0))
        assert rule.available_at(date(2024, 1, 31)) == ist(2024, 2, 1, 0, 30)

    def test_the_switch_follows_the_us_dates_not_the_indian_ones(self) -> None:
        # US DST began on 2024-03-10 and ended on 2024-11-03; Europe's differ, and India has none.
        rule = ZonedRelease(time(8, 30))
        assert rule.available_at(date(2024, 3, 8)) == ist(2024, 3, 8, 19, 0)  # still EST
        assert rule.available_at(date(2024, 3, 11)) == ist(2024, 3, 11, 18, 0)  # EDT
        assert rule.available_at(date(2024, 11, 1)) == ist(2024, 11, 1, 18, 0)  # EDT
        assert rule.available_at(date(2024, 11, 4)) == ist(2024, 11, 4, 19, 0)  # EST

    def test_an_indian_release_is_at_its_ist_time(self) -> None:
        assert IstRelease(time(10, 0)).available_at(date(2025, 2, 7)) == ist(2025, 2, 7, 10, 0)

    def test_a_fact_known_ahead_is_available_from_the_start_of_its_day(self) -> None:
        assert KnownAhead().available_at(date(2024, 1, 25)) == ist(2024, 1, 25, 0, 0)

    def test_an_event_without_a_timezone_or_a_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            CalendarEvent("k", "n", date(2024, 1, 1), datetime(2024, 1, 1), SRC, CHECKED)
        with pytest.raises(ValueError, match="source"):
            CalendarEvent("k", "n", date(2024, 1, 1), ist(2024, 1, 1, 0, 0), "", CHECKED)


def event(kind: str, day: date, at: datetime | None = None) -> CalendarEvent:
    return CalendarEvent(
        kind, "n", day, at or ist(day.year, day.month, day.day, 0, 0), SRC, CHECKED
    )


def test_a_decision_may_know_only_what_became_public_before_it(tmp_path: Path) -> None:
    calendar = YamlCalendar(tmp_path / "c.yaml")
    calendar.write(
        [event("a", date(2024, 1, 31), ist(2024, 2, 1, 0, 30)), event("b", date(2024, 2, 1))]
    )

    known = calendar.available_between(ist(2024, 2, 1, 0, 0), ist(2024, 2, 1, 9, 15))

    assert [e.kind for e in known] == ["a", "b"]  # the 00:30 statement and the 00:00 fact
    assert calendar.available_between(ist(2024, 2, 1, 0, 0), ist(2024, 2, 1, 0, 30)) == [
        known[1]
    ]  # a decision at 00:30 sharp does not know a release AT 00:30


def test_the_yaml_round_trips_and_is_sorted(tmp_path: Path) -> None:
    calendar = YamlCalendar(tmp_path / "c.yaml")
    rows = [event("b", date(2024, 5, 1)), event("a", date(2024, 1, 1))]

    assert calendar.write(rows) == 2

    assert [e.event_id for e in calendar.read()] == ["a:2024-01-01", "b:2024-05-01"]
    assert calendar.read()[0].available_at == rows[1].available_at


CURRENT = """
<h4><a id="1">2024 FOMC Meetings</a></h4>
<div class="row fomc-meeting" "><div class="fomc-meeting__month col"><strong>March</strong></div>
<div class="fomc-meeting__date col">19-20*</div></div>
<div class="row fomc-meeting" "><div class="fomc-meeting__month col"><strong>Apr/May</strong></div>
<div class="fomc-meeting__date col">30-1</div></div>
"""
HISTORICAL = """
<h5>January 28-29 Meeting - 2020</h5><h5>March 15 (unscheduled) Meeting - 2020</h5>
<h5>March 17-18 (cancelled) Meeting - 2020</h5><h5>March 19 (notation vote) - 2020</h5>
<h5>Jul/Aug 31-1 Meeting - 2018</h5>
"""


def test_the_fomc_pages_parse_to_decision_days() -> None:
    assert [m.decision_day for m in parse_current_page(CURRENT)] == [
        date(2024, 3, 20), date(2024, 5, 1),
    ]  # fmt: skip
    found = parse_historical_page(HISTORICAL)
    assert [(m.decision_day, m.scheduled) for m in found] == [
        (date(2020, 1, 29), True), (date(2020, 3, 15), False), (date(2018, 8, 1), True),
    ]  # fmt: skip


def test_the_fed_source_keeps_scheduled_meetings_only_and_a_manual_row_adds_the_emergency_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "fed").mkdir()
    (tmp_path / "fed" / "fomchistorical2020.htm").write_text(HISTORICAL)
    manual = tmp_path / "manual.yaml"
    manual.write_text(
        "events:\n  - {kind: fomc, name: emergency, date: 2020-03-15, at: '17:00', "
        "zone: America/New_York, source: https://x.org/p, checked_on: 2026-09-26}\n"
    )

    events = CalendarBuilder(
        [FedSource(tmp_path, CHECKED), ManualSource(manual)], date(2019, 1, 1), date(2026, 3, 18)
    ).build()

    days = [e.event_date for e in events]
    assert date(2020, 3, 15) in days and date(2018, 8, 1) not in days  # 2018 is before this span
    emergency = next(e for e in events if e.event_date == date(2020, 3, 15))
    assert emergency.available_at == ist(2020, 3, 16, 2, 30)


def test_a_manual_row_missing_a_field_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text("events:\n  - {kind: budget, name: b, date: 2024-02-01, zone: IST}\n")
    with pytest.raises(ValueError, match="source"):
        ManualSource(path).events()


def test_the_committed_manual_rows_load() -> None:
    assert ManualSource(Path("config/calendar/manual_events.yaml")).events()


NOTICE = "https://www.niftyindices.com/Press_Release/ind_prs01092022.pdf"


def test_index_notices_count_from_the_end_of_their_day_and_effective_days_are_known_ahead(
    tmp_path: Path,
) -> None:
    from emporos.research.cause_ledger.sources import IndexChangeSource

    path = tmp_path / "changes.yaml"
    path.write_text(
        "changes:\n"
        "- {index: NIFTY 100, effective: '2022-09-30', added: [A], removed: [B],"
        f" source: '{NOTICE} fetched 2026-09-25'}}\n"
        "- {index: NIFTY MIDCAP 150, effective: '2022-09-30', added: [B], removed: [C],"
        f" source: '{NOTICE} fetched 2026-09-25'}}\n"
    )

    events = {e.kind: e for e in IndexChangeSource(path, CHECKED).events()}

    assert events["index_notice"].event_date == date(2022, 9, 1)
    assert events["index_notice"].available_at == ist(2022, 9, 1, 23, 59)
    assert events["index_effective"].available_at == ist(2022, 9, 30, 0, 0)
    assert "NIFTY 100 +A -B" in events["index_notice"].note
    assert events["index_notice"].source.endswith("ind_prs01092022.pdf")


def test_us_cpi_and_payrolls_release_at_0830_new_york_from_alfred_dates(tmp_path: Path) -> None:
    from emporos.research.cause_ledger.sources import AlfredSource

    (tmp_path / "alfred").mkdir()
    (tmp_path / "alfred" / "cpi-release-dates.txt").write_text(
        "Release: Consumer Price Index\n2024-01-11\n2024-07-11\nnot a date\n"
    )
    (tmp_path / "alfred" / "employment-dates.txt").write_text("2024-03-08\n")

    events = {(e.kind, e.event_date): e for e in AlfredSource(tmp_path, CHECKED).events()}

    assert events[("us_cpi", date(2024, 1, 11))].available_at == ist(2024, 1, 11, 19, 0)  # EST
    assert events[("us_cpi", date(2024, 7, 11))].available_at == ist(2024, 7, 11, 18, 0)  # EDT
    assert events[("us_payrolls", date(2024, 3, 8))].available_at == ist(2024, 3, 8, 19, 0)
    assert len(events) == 3 and all("alfred.stlouisfed.org" in e.source for e in events.values())


MOSPI_TEXT = """
 1       April      12th April     All India Consumer Price Index (CPI)#
         2024                      All India Index of Industrial Production (IIP)*
 2       May        12th May       All India Consumer Price Index (CPI)#
                    31th May       - Provisional Estimates of GDP (2023-24)
                                   - Q4 Estimates of GDP
 10      January    12th Jan       All India Consumer Price Index (CPI)#
"""


def test_mospi_cpi_and_gdp_days_are_read_from_the_advance_calendar_text() -> None:
    from emporos.research.cause_ledger.mospi import parse_advance_release_calendar

    found = [(r.kind, r.day) for r in parse_advance_release_calendar(MOSPI_TEXT, 2024)]

    assert found == [
        ("india_cpi", date(2024, 4, 12)), ("india_cpi", date(2024, 5, 12)),
        ("india_gdp", date(2024, 5, 31)), ("india_cpi", date(2025, 1, 12)),
    ]  # fmt: skip


def test_mospi_events_count_from_1600_ist(tmp_path: Path) -> None:
    from emporos.research.cause_ledger.sources import MospiSource
    from emporos.research.filings.pdf_text import ExtractedText

    class Fake:
        def extract(self, pdf: bytes) -> ExtractedText:
            return ExtractedText(MOSPI_TEXT, False, "h", len(pdf))

    (tmp_path / "mospi").mkdir()
    (tmp_path / "mospi" / "arc-2024-25.pdf").write_bytes(b"%PDF")

    events = MospiSource(tmp_path, CHECKED, Fake()).events()

    assert len(events) == 4 and events[0].available_at == ist(2024, 4, 12, 16, 0)
    assert all(e.source.startswith("https://mospi.gov.in") for e in events)
