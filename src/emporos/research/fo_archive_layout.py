"""Where the exchange posts each day's F&O bhavcopy (EM-225). Public files only.

Two URL layouts exist. UDiFF (`/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip`) is the
current one and answers 404 for days before mid-2024. The legacy layout
(`/content/historical/DERIVATIVES/<YYYY>/<MON>/fo<DD><MON><YYYY>bhav.csv.zip`) holds the earlier
years. Around the switch both may exist, so a day near it lists both candidates and the fetcher
takes
the first that is present."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from emporos.research.fo_archive_rows import ArchiveFormat

__all__ = ["ArchiveCandidate", "ArchiveLayout", "CutoverLayout", "HOST"]

HOST = "https://nsearchives.nseindia.com"
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


@dataclass(frozen=True)
class ArchiveCandidate:
    url: str
    format: ArchiveFormat


class ArchiveLayout(Protocol):
    def candidates(self, day: date) -> list[ArchiveCandidate]:
        """The files that may hold `day`'s bhavcopy, most likely first."""
        ...


def udiff_url(day: date) -> str:
    return f"{HOST}/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def legacy_url(day: date) -> str:
    month = _MONTHS[day.month - 1]
    return (
        f"{HOST}/content/historical/DERIVATIVES/{day.year}/{month}/"
        f"fo{day.day:02d}{month}{day.year}bhav.csv.zip"
    )


@dataclass(frozen=True)
class CutoverLayout:
    """Legacy before `cutover`, UDiFF from it, and both in the `overlap_days` before it."""

    cutover: date = date(2024, 7, 8)
    overlap_days: int = 7

    def __post_init__(self) -> None:
        if self.overlap_days < 0:
            raise ValueError("the overlap cannot be negative")

    def candidates(self, day: date) -> list[ArchiveCandidate]:
        udiff = ArchiveCandidate(udiff_url(day), ArchiveFormat.UDIFF)
        legacy = ArchiveCandidate(legacy_url(day), ArchiveFormat.LEGACY)
        if day >= self.cutover:
            return [udiff]
        if day >= self.cutover - timedelta(days=self.overlap_days):
            return [legacy, udiff]
        return [legacy]
