"""India CPI and GDP release days from MoSPI's Advance Release Calendar PDFs (EM-244).

The calendar is a table whose rows read `12th April   All India Consumer Price Index (CPI)`; a
date can head several lines (`31th May  - Provisional Estimates of GDP` / `- Q4 Estimates of GDP`).
The financial year names the year: April-December fall in its first calendar year, January-March in
the second. Only the two market-moving series are read: CPI and the quarterly GDP estimates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

__all__ = ["MospiRelease", "parse_advance_release_calendar"]

_MONTHS = {
    m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}  # fmt: skip
_DATE = re.compile(r"(?<![\d])(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3,9})\b")


@dataclass(frozen=True)
class MospiRelease:
    kind: str  # "india_cpi" or "india_gdp"
    day: date


def parse_advance_release_calendar(text: str, financial_year_start: int) -> list[MospiRelease]:
    found: dict[tuple[str, date], MospiRelease] = {}
    current: date | None = None
    for line in text.splitlines():
        match = _DATE.search(line)
        if match and match.group(2)[:3].lower() in _MONTHS:
            month = _MONTHS[match.group(2)[:3].lower()]
            year = financial_year_start if month >= 4 else financial_year_start + 1
            try:
                current = date(year, month, int(match.group(1)))
            except ValueError:
                current = None
        if current is None:
            continue
        lowered = line.lower()
        kind = None
        if "consumer price index" in lowered:
            kind = "india_cpi"
        elif "estimates of gdp" in lowered or "gross domestic product" in lowered:
            kind = "india_gdp"
        if kind is not None:
            found[(kind, current)] = MospiRelease(kind, current)
    return sorted(found.values(), key=lambda r: (r.day, r.kind))
