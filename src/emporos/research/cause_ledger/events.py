"""Calendar events with the time each became PUBLIC (driver-atlas-plan §3.2, §3.3; EM-244).

`CalendarEvent.available_at` is when an ordinary participant could first have known the fact, in
IST: a scheduled release at its scheduled time (a US release in New York time, so daylight-saving
correct), an exchange notice when it was issued, a fact known long ahead (an expiry date) at the
start of its own day. Each row carries the URL it was read from and the day it was read."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from emporos.core.clock import IST

__all__ = [
    "DEFAULT_CALENDAR_FILE", "CalendarEvent", "IstRelease", "KnownAhead", "ReleaseRule",
    "YamlCalendar", "ZonedRelease",
]  # fmt: skip

DEFAULT_CALENDAR_FILE = Path("config/calendar/macro_events.yaml")
NEW_YORK = ZoneInfo("America/New_York")


class ReleaseRule:
    """How a kind of event maps its local date to the IST instant it became public."""

    def available_at(self, event_date: date) -> datetime:
        raise NotImplementedError


@dataclass(frozen=True)
class ZonedRelease(ReleaseRule):
    """Released at `local` time in `zone` on the event date (DST follows the zone's own rules)."""

    local: time
    zone: ZoneInfo = NEW_YORK

    def available_at(self, event_date: date) -> datetime:
        return datetime.combine(event_date, self.local, tzinfo=self.zone).astimezone(IST)


@dataclass(frozen=True)
class IstRelease(ReleaseRule):
    local: time

    def available_at(self, event_date: date) -> datetime:
        return datetime.combine(event_date, self.local, tzinfo=IST)


class KnownAhead(ReleaseRule):
    """A fact published well before its date (an expiry): available from the start of its own day
    at the latest. The true notice date is earlier; this is the conservative bound."""

    def available_at(self, event_date: date) -> datetime:
        return datetime.combine(event_date, time(0, 0), tzinfo=IST)


@dataclass(frozen=True)
class CalendarEvent:
    kind: str
    name: str
    event_date: date  # the local calendar date of the event in its own place
    available_at: datetime  # tz-aware
    source: str
    checked_on: date
    value: str = ""  # a number the event carries (a policy rate), as text
    note: str = ""

    def __post_init__(self) -> None:
        if self.available_at.tzinfo is None:
            raise ValueError("an event's availability time must carry a timezone")
        if not self.source.startswith("http"):
            raise ValueError(f"{self.kind} {self.event_date}: every row needs a source URL")

    @property
    def event_id(self) -> str:
        return f"{self.kind}:{self.event_date.isoformat()}"

    def as_document(self) -> dict[str, object]:
        doc: dict[str, object] = {
            "id": self.event_id, "kind": self.kind, "name": self.name,
            "date": self.event_date.isoformat(),
            "available_at": self.available_at.astimezone(IST).isoformat(),
            "source": self.source, "checked_on": self.checked_on.isoformat(),
        }  # fmt: skip
        if self.value:
            doc["value"] = self.value
        if self.note:
            doc["note"] = self.note
        return doc

    @staticmethod
    def from_document(doc: dict[str, object]) -> CalendarEvent:
        available = doc["available_at"]
        assert isinstance(available, datetime | str)
        stamp = available if isinstance(available, datetime) else datetime.fromisoformat(available)
        return CalendarEvent(
            str(doc["kind"]), str(doc["name"]), _day(doc["date"]), stamp, str(doc["source"]),
            _day(doc["checked_on"]), str(doc.get("value", "")), str(doc.get("note", "")),
        )  # fmt: skip


def _day(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class YamlCalendar:
    """A sorted YAML file of events, written whole (a rebuild replaces it)."""

    def __init__(self, path: Path = DEFAULT_CALENDAR_FILE) -> None:
        self._path = path

    def write(self, events: Iterable[CalendarEvent], header: str = "") -> int:
        ordered = sorted(
            {e.event_id: e for e in events}.values(), key=lambda e: (e.event_date, e.kind)
        )
        body = yaml.safe_dump(
            {"events": [e.as_document() for e in ordered]}, sort_keys=False, width=200
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(header + body, encoding="utf-8")
        return len(ordered)

    def read(self) -> list[CalendarEvent]:
        document = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        return [CalendarEvent.from_document(d) for d in document["events"]]

    def available_between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """The events that became public in [start, end): what a decision at `end` may know."""
        return [e for e in self.read() if start <= e.available_at < end]
