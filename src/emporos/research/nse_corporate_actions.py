"""The exchange's corporate-actions feed, one polite request per name (EM-221, A-F2).

The operator ruled on 2026-09-25 (PROFIT_PLAN §8) that public data may be collected for this
private research. The rules that ruling sets are kept here as code: an honest user agent, requests
spaced by an injected sleeper and never closer than a second, nothing retried, and a refusal
(401, 403, 429) stops the whole run instead of being worked around. Run it from the development
machine only, never from the production host."""

from __future__ import annotations

from datetime import date
from typing import Protocol
from urllib.parse import quote

import httpx

from emporos.core.clock import Sleeper
from emporos.research.corporate_actions import CorporateAction, parse_actions

__all__ = ["ActionsRefused", "CorporateActionSource", "NseCorporateActionSource"]

ENDPOINT = "https://www.nseindia.com/api/corporates-corporateActions"
USER_AGENT = "emporos-research/1.0 (personal research, one request every few seconds)"
_DAY = "%d-%m-%Y"


class ActionsRefused(RuntimeError):
    """The exchange declined to serve the request; the run must stop, not retry."""


class CorporateActionSource(Protocol):
    async def actions(self, symbol: str, first: date, last: date) -> list[CorporateAction]:
        """Every corporate action for `symbol` with an ex-date in [first, last], oldest first."""
        ...

    def url_for(self, symbol: str, first: date, last: date) -> str:
        """The address a request for these arguments goes to: recorded with what it returned."""
        ...


class NseCorporateActionSource:
    def __init__(
        self, client: httpx.AsyncClient, sleeper: Sleeper, seconds_between_requests: float = 3.0
    ) -> None:
        if seconds_between_requests < 1.0:
            raise ValueError("requests must be at least a second apart")
        self._client = client
        self._sleeper = sleeper
        self._gap = seconds_between_requests
        self._requests = 0

    def url_for(self, symbol: str, first: date, last: date) -> str:
        return (
            f"{ENDPOINT}?index=equities&symbol={quote(symbol, safe='')}"
            f"&from_date={first.strftime(_DAY)}&to_date={last.strftime(_DAY)}"
        )

    async def actions(self, symbol: str, first: date, last: date) -> list[CorporateAction]:
        if self._requests:
            await self._sleeper.sleep(self._gap)
        self._requests += 1
        response = await self._client.get(
            self.url_for(symbol, first, last), headers={"User-Agent": USER_AGENT}
        )
        if response.status_code in (401, 403, 429):
            raise ActionsRefused(
                f"{symbol}: the exchange answered {response.status_code}; stopping the run"
            )
        response.raise_for_status()
        return parse_actions(response.json(), symbol)
