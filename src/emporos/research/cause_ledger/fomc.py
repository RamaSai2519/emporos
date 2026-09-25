"""FOMC decision days from the Federal Reserve's own public calendar pages (EM-244).

`fomccalendars.htm` lists the current and recent years; `fomchistorical<year>.htm` the older ones.
A scheduled meeting's statement is released at 14:00 New York time on its LAST day, so the IST
availability is 23:30 on that day in US daylight time and 00:30 the next calendar day in standard
time. An unscheduled meeting is recorded with the time of its own press release."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time

__all__ = ["FOMC_STATEMENT_TIME", "FomcMeeting", "parse_current_page", "parse_historical_page"]

FOMC_STATEMENT_TIME = time(14, 0)
_MONTHS = {
    m: i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August", "September",
         "October", "November", "December"], 1,
    )
}  # fmt: skip
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class FomcMeeting:
    decision_day: date  # the last day of the meeting
    scheduled: bool


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", fragment)).strip()


def _month(name: str) -> int | None:
    """The month of a label like `March`, `Jan/Feb` or `Oct/Nov`: the LAST one named (a meeting
    that spans two months is decided in the second)."""
    last = name.split("/")[-1].strip()
    return _MONTHS.get(_FULL.get(last[:3], last))


_FULL = {m[:3]: m for m in _MONTHS}


def parse_current_page(html: str) -> list[FomcMeeting]:
    """The meetings of every `<year> FOMC Meetings` panel: month label, then the day range, whose
    last number is the decision day."""
    meetings: list[FomcMeeting] = []
    parts = re.split(r'<h4><a id="\d+">(\d{4}) FOMC Meetings</a></h4>', html)
    for year_text, body in zip(parts[1::2], parts[2::2], strict=False):
        pairs = re.findall(
            r"fomc-meeting__month[^>]*>(.*?)</div>.*?fomc-meeting__date[^>]*>(.*?)</div>",
            body,
            re.S,
        )
        for month_html, days_html in pairs:
            month, days = _month(_text(month_html)), _text(days_html)
            numbers = re.findall(r"\d+", days)
            if month is None or not numbers:
                continue
            meetings.append(
                FomcMeeting(
                    date(int(year_text), month, int(numbers[-1])), "unscheduled" not in days.lower()
                )
            )
    return meetings


def parse_historical_page(html: str) -> list[FomcMeeting]:
    """The `<h5>` headings of a historical page: `January 28-29 Meeting - 2020`,
    `Jul/Aug 31-1 Meeting - 2018` (decided on the 1st of the second month),
    `March 15 (unscheduled) Meeting - 2020`. A cancelled meeting and a notation vote are skipped."""
    meetings: list[FomcMeeting] = []
    for heading in re.findall(r"<h5[^>]*>(.*?)</h5>", html, re.S):
        text = _text(heading)
        if "notation" in text or "cancelled" in text or " Meeting - " not in text:
            continue
        found = re.match(r"([A-Za-z/]+) (\d+)(?:-(\d+))?", text)
        month = _month(found.group(1)) if found else None
        if found is None or month is None:
            continue
        day = int(found.group(3) or found.group(2))
        meetings.append(
            FomcMeeting(date(int(text.rsplit("- ", 1)[1]), month, day), "unscheduled" not in text)
        )
    return meetings
