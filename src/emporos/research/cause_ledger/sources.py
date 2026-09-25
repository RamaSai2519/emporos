"""Where calendar events come from (EM-244): a parsed public page, or a hand-kept YAML.

An `EventSource` yields `CalendarEvent`s; `CalendarBuilder` merges any number of them into one
sorted file within the calendar's span. A source never invents an availability time: the rule that
makes it (`ZonedRelease`, `IstRelease`, `KnownAhead`) is named where the source is built."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import yaml

from emporos.research.cause_ledger.events import (
    CalendarEvent,
    IstRelease,
    KnownAhead,
    ReleaseRule,
    ZonedRelease,
)
from emporos.research.cause_ledger.fomc import (
    FOMC_STATEMENT_TIME,
    parse_current_page,
    parse_historical_page,
)
from emporos.research.cause_ledger.pages import PageSpec

__all__ = [
    "CALENDAR_FIRST", "CALENDAR_LAST", "CalendarBuilder", "EventSource", "FedSource",
    "ManualSource",
]  # fmt: skip

CALENDAR_FIRST = date(2017, 11, 1)
CALENDAR_LAST = date(2026, 3, 18)
FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_HISTORICAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
FED_HISTORICAL_YEARS = (2017, 2018, 2019, 2020)


class EventSource(Protocol):
    def events(self) -> Sequence[CalendarEvent]: ...


class FedSource:
    """FOMC decision days from the raw pages `fed_pages()` names, read from `raw_root`."""

    def __init__(self, raw_root: Path, checked_on: date) -> None:
        self._root, self._checked = raw_root, checked_on

    @staticmethod
    def pages() -> list[PageSpec]:
        pages = [PageSpec("fed", "fed/fomccalendars.htm", FED_CALENDAR_URL)]
        pages += [
            PageSpec("fed", f"fed/fomchistorical{y}.htm", FED_HISTORICAL_URL.format(year=y))
            for y in FED_HISTORICAL_YEARS
        ]
        return pages

    def events(self) -> Sequence[CalendarEvent]:
        rule = ZonedRelease(FOMC_STATEMENT_TIME)
        out: list[CalendarEvent] = []
        for spec, parse in [
            (p, parse_historical_page if "historical" in p.name else parse_current_page)
            for p in self.pages()
        ]:
            path = self._root / spec.name
            if not path.exists():
                continue
            for meeting in parse(path.read_text(encoding="utf-8", errors="replace")):
                if not meeting.scheduled:
                    continue  # an emergency meeting is a manual row with its own release time
                out.append(
                    CalendarEvent(
                        "fomc",
                        "FOMC statement",
                        meeting.decision_day,
                        rule.available_at(meeting.decision_day),
                        spec.url,
                        self._checked,
                    )  # fmt: skip
                )
        return out


class ManualSource:
    """Rows kept by hand in a YAML file: `kind`, `name`, `date`, `at` (HH:MM), `zone` (`IST`, an
    IANA zone name, or `ahead` for a fact known in advance), `source`, `checked_on`, and optionally
    `value` and `note`. A row missing any required field is an error, not a guess."""

    REQUIRED = ("kind", "name", "date", "zone", "source", "checked_on")

    def __init__(self, path: Path) -> None:
        self._path = path

    def events(self) -> Sequence[CalendarEvent]:
        document = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        out: list[CalendarEvent] = []
        for row in document["events"]:
            missing = [k for k in self.REQUIRED if k not in row]
            if missing:
                raise ValueError(f"{self._path}: {row.get('kind')} {row.get('date')}: no {missing}")
            when = _day(row["date"])
            out.append(
                CalendarEvent(
                    row["kind"],
                    row["name"],
                    when,
                    self._rule(row).available_at(when),
                    row["source"],
                    _day(row["checked_on"]),
                    str(row.get("value", "")),
                    str(row.get("note", "")),
                )  # fmt: skip
            )
        return out

    @staticmethod
    def _rule(row: dict[str, object]) -> ReleaseRule:
        zone = str(row["zone"])
        if zone == "ahead":
            return KnownAhead()
        hours, minutes = str(row.get("at", "")).split(":") if row.get("at") else (None, None)
        if hours is None or minutes is None:
            raise ValueError(f"{row['kind']} {row['date']}: a timed row needs `at`")
        local = time(int(hours), int(minutes))
        return IstRelease(local) if zone == "IST" else ZonedRelease(local, ZoneInfo(zone))


def _day(value: object) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


class CalendarBuilder:
    def __init__(
        self,
        sources: Sequence[EventSource],
        first: date = CALENDAR_FIRST,
        last: date = CALENDAR_LAST,
    ) -> None:
        self._sources, self._first, self._last = sources, first, last

    def build(self) -> list[CalendarEvent]:
        """Every source's events with a date inside the span, one per (kind, date)."""
        merged: dict[str, CalendarEvent] = {}
        for source in self._sources:
            for event in source.events():
                if self._first <= event.event_date <= self._last:
                    merged[event.event_id] = event
        return sorted(merged.values(), key=lambda e: (e.event_date, e.kind))
