"""GST Council meeting dates from the Council's own public page (EM-244).

`https://gstcouncil.gov.in/gst-council-meetings` lists each meeting with its date, e.g.
`50th GST Council Meeting 11th July 2023` or `47th GST Council Meeting 28th & 29th June 2022`; a
two-day meeting counts from its LAST day. The page's list ends at the 51st meeting (2023-08-02); later
ones are hand-kept rows. The decisions are announced at a press conference after the meeting, whose
time the page does not give, so the calendar counts them from 23:59 IST of that day."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import date

__all__ = ["GstMeeting", "parse_gst_meetings"]

_MONTHS = {
    m: i for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
    )
}  # fmt: skip
_MEETING = re.compile(
    r"(\d+)(?:st|nd|rd|th) GST Council Meeting "
    r"(\d{1,2})(?:st|nd|rd|th)?(?: &(?:amp;)? (\d{1,2})(?:st|nd|rd|th)?)? ([A-Za-z]+) (\d{4})"
)


@dataclass(frozen=True)
class GstMeeting:
    number: int
    last_day: date


def parse_gst_meetings(page: str) -> list[GstMeeting]:
    text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", page)))
    found: dict[int, GstMeeting] = {}
    for number, first, second, month, year in _MEETING.findall(text):
        month_number = _MONTHS.get(month[:3])
        if month_number is None:
            continue
        found[int(number)] = GstMeeting(
            int(number), date(int(year), month_number, int(second or first))
        )
    return [found[n] for n in sorted(found)]
