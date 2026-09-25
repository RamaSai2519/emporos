"""NSE's public corporate-announcements feed, one request per name and window (§8, EM-239).

The operator authorised this endpoint on 2026-09-24 (results) and ruled on 2026-09-25 that public
data may be collected for this program's private research. It answers a plain GET with each
filing's company time, exchange dissemination time, category, text and attachment link. The reply is
returned as bytes, verbatim, for the raw store; parsing is `parse_nse_filings`."""

from __future__ import annotations

from datetime import date
from typing import Protocol
from urllib.parse import quote

from emporos.research.filings.polite import PoliteGet

__all__ = ["FilingSource", "NseFilingSource"]

ENDPOINT = "https://www.nseindia.com/api/corporate-announcements"
_DAY = "%d-%m-%Y"


class FilingSource(Protocol):
    source: str

    def url_for(self, symbol: str, first: date, last: date) -> str: ...

    async def fetch(self, symbol: str, first: date, last: date) -> bytes:
        """The source's reply for `symbol` over [first, last], verbatim."""
        ...


class NseFilingSource:
    source = "NSE"

    def __init__(self, getter: PoliteGet) -> None:
        self._getter = getter

    def url_for(self, symbol: str, first: date, last: date) -> str:
        return (
            f"{ENDPOINT}?index=equities&symbol={quote(symbol, safe='')}"
            f"&from_date={first.strftime(_DAY)}&to_date={last.strftime(_DAY)}"
        )

    async def fetch(self, symbol: str, first: date, last: date) -> bytes:
        return await self._getter.get(self.url_for(symbol, first, last))
