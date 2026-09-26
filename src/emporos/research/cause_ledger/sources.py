"""Where calendar events come from (EM-244): a parsed public page, or a hand-kept YAML.

An `EventSource` yields `CalendarEvent`s; `CalendarBuilder` merges any number of them into one
sorted file within the calendar's span. A source never invents an availability time: the rule that
makes it (`ZonedRelease`, `IstRelease`, `KnownAhead`) is named where the source is built."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, time
from pathlib import Path
from typing import Any, Protocol
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
from emporos.research.cause_ledger.gst import parse_gst_meetings
from emporos.research.cause_ledger.pages import PageSpec

__all__ = [
    "CALENDAR_FIRST", "CALENDAR_LAST", "CalendarBuilder", "EventSource", "FedSource",
    "AlfredSource", "GstSource", "IndexChangeSource", "ManualSource",
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


class AlfredSource:
    """US CPI and payroll release days from the St. Louis Fed's ALFRED release-date lists (a plain
    text file of one `YYYY-MM-DD` per release; the BLS's own schedule pages refuse bots). BLS
    releases both at 08:30 New York time, so 18:00 IST in US summer time and 19:00 IST otherwise."""

    RELEASES = (
        ("us_cpi", "US CPI", 10, "alfred/cpi-release-dates.txt"),
        ("us_payrolls", "US payrolls (employment situation)", 50, "alfred/employment-dates.txt"),
    )  # fmt: skip
    URL = "https://alfred.stlouisfed.org/release/downloaddates?rid={rid}&ff=txt"
    RELEASE_TIME = time(8, 30)

    def __init__(self, raw_root: Path, checked_on: date) -> None:
        self._root, self._checked = raw_root, checked_on

    @classmethod
    def pages(cls) -> list[PageSpec]:
        return [
            PageSpec("alfred", name, cls.URL.format(rid=rid)) for _, _, rid, name in cls.RELEASES
        ]

    def events(self) -> Sequence[CalendarEvent]:
        rule = ZonedRelease(self.RELEASE_TIME)
        out: list[CalendarEvent] = []
        for kind, name, rid, path_name in self.RELEASES:
            path = self._root / path_name
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", line.strip()):
                    day = date.fromisoformat(line.strip())
                    out.append(
                        CalendarEvent(
                            kind,
                            name,
                            day,
                            rule.available_at(day),
                            self.URL.format(rid=rid),
                            self._checked,
                            note="release day per ALFRED; 08:30 New York time",
                        )  # fmt: skip
                    )
        return out


class GstSource:
    """GST Council meetings from the Council's page (`gst.py`), counted from 23:59 IST of the last
    day of each meeting."""

    URL = "https://gstcouncil.gov.in/gst-council-meetings"

    def __init__(self, raw_root: Path, checked_on: date) -> None:
        self._root, self._checked = raw_root, checked_on

    @classmethod
    def pages(cls) -> list[PageSpec]:
        return [PageSpec("gst", "gst/gst-council-meetings.htm", cls.URL)]

    def events(self) -> Sequence[CalendarEvent]:
        path = self._root / self.pages()[0].name
        if not path.exists():
            return []
        rule = IstRelease(time(23, 59))
        return [
            CalendarEvent(
                "gst_council",
                f"GST Council meeting {m.number}",
                m.last_day,
                rule.available_at(m.last_day),
                self.URL,
                self._checked,
                note="press conference time not on the page; counted from 23:59 IST",
            )  # fmt: skip
            for m in parse_gst_meetings(path.read_text(encoding="utf-8", errors="replace"))
        ]


class IndexChangeSource:
    """NIFTY 100 / Midcap 150 constituent changes from `config/universe/d1/index-changes.yaml`: one
    `index_notice` event on the day the notice was published (the date is in its file name,
    `ind_prs<ddmmyyyy>`; the time of day is not on the notice, so it counts from 23:59 IST, i.e.
    for the next session) and one `index_effective` event on the effective day (known ahead)."""

    NOTICE = re.compile(r"ind_prs(\d{2})(\d{2})(\d{4})")

    def __init__(self, path: Path, checked_on: date) -> None:
        self._path, self._checked = path, checked_on

    def events(self) -> Sequence[CalendarEvent]:
        changes = yaml.safe_load(self._path.read_text(encoding="utf-8"))["changes"]
        by_notice: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for change in changes:
            by_notice[str(change["source"]).split()[0]].append(change)
        out: list[CalendarEvent] = []
        for url, group in by_notice.items():
            found = self.NOTICE.search(url)
            if found is None:
                raise ValueError(f"{url}: no notice date in the file name")
            day = date(int(found.group(3)), int(found.group(2)), int(found.group(1)))
            detail = "; ".join(
                f"{c['index']} +{','.join(c['added'])} -{','.join(c['removed'])}" for c in group
            )
            out.append(
                CalendarEvent(
                    "index_notice",
                    "NIFTY index change notice",
                    day,
                    IstRelease(time(23, 59)).available_at(day),
                    url,
                    self._checked,
                    note=detail + "; time of day not on the notice, counted from 23:59 IST",
                )  # fmt: skip
            )
            for effective in sorted({str(c["effective"]) for c in group}):
                when = date.fromisoformat(effective)
                out.append(
                    CalendarEvent(
                        "index_effective",
                        "NIFTY index change effective",
                        when,
                        KnownAhead().available_at(when),
                        url,
                        self._checked,
                        note=detail,
                    )  # fmt: skip
                )
        return out


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
