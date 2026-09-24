"""The exchange's corporate-announcements feed, one polite request per name (EM-191 D5).

The operator authorised using NSE's public announcements endpoint on 2026-09-24. It answers a plain
GET with each filing's dissemination time, and the `subject` filter narrows the reply to results
filings, so ten years for one name is one small request. It is used sparingly: requests are spaced
by an injected sleeper, the client identifies itself honestly, nothing is retried, and a refusal
(403 or 429) stops the whole run instead of being worked around."""

from __future__ import annotations

from datetime import date
from typing import Protocol
from urllib.parse import quote

import httpx

from emporos.core.clock import Sleeper
from emporos.research.results_filings import RESULTS_SUBJECT, ResultsFiling, parse_filings

__all__ = ["AnnouncementRefused", "AnnouncementSource", "NseAnnouncementSource"]

ENDPOINT = "https://www.nseindia.com/api/corporate-announcements"
USER_AGENT = "emporos-research/1.0 (personal research, one request every few seconds)"
_DAY = "%d-%m-%Y"


class AnnouncementRefused(RuntimeError):
    """The exchange declined to serve the request; the run must stop, not retry."""


class AnnouncementSource(Protocol):
    async def results_filings(
        self, symbol: str, first: date, last: date, subject: str = RESULTS_SUBJECT
    ) -> list[ResultsFiling]:
        """Every results filing for `symbol` under `subject` in [first, last], oldest first."""
        ...


class NseAnnouncementSource:
    def __init__(
        self, client: httpx.AsyncClient, sleeper: Sleeper, seconds_between_requests: float = 3.0
    ) -> None:
        if seconds_between_requests < 1.0:
            raise ValueError("requests must be at least a second apart")
        self._client = client
        self._sleeper = sleeper
        self._gap = seconds_between_requests
        self._requests = 0

    async def results_filings(
        self, symbol: str, first: date, last: date, subject: str = RESULTS_SUBJECT
    ) -> list[ResultsFiling]:
        if self._requests:
            await self._sleeper.sleep(self._gap)
        self._requests += 1
        url = (
            f"{ENDPOINT}?index=equities&symbol={quote(symbol, safe='')}"
            f"&from_date={first.strftime(_DAY)}&to_date={last.strftime(_DAY)}"
            f"&subject={quote(subject)}"
        )
        response = await self._client.get(url, headers={"User-Agent": USER_AGENT})
        if response.status_code in (401, 403, 429):
            raise AnnouncementRefused(
                f"{symbol}: the exchange answered {response.status_code}; stopping the run"
            )
        response.raise_for_status()
        return parse_filings(response.json(), symbol, subject)
