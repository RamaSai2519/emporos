"""One polite GET at a time (PROFIT_PLAN §8), shared by every Track L collector.

The rules of the data-collection ruling are kept here as code: an honest user agent, requests spaced
by an injected sleeper and never closer than a second, nothing retried, and a refusal (401, 403,
429) stops the whole run (`SourceRefused`) instead of being worked around. A 404 is a fact about the
address (`NotFound`), not a refusal. Run from the development machine only."""

from __future__ import annotations

import httpx

from emporos.core.clock import Sleeper

__all__ = ["NotFound", "PoliteGet", "SourceRefused", "USER_AGENT"]

USER_AGENT = "emporos-research/1.0 (personal research, one request every few seconds)"


class SourceRefused(RuntimeError):
    """The source declined to serve the request; the run must stop, not retry."""


class NotFound(RuntimeError):
    """The address has nothing (a 404): recorded, not retried, not a refusal."""


class PoliteGet:
    def __init__(
        self, client: httpx.AsyncClient, sleeper: Sleeper, seconds_between_requests: float = 3.0
    ) -> None:
        if seconds_between_requests < 1.0:
            raise ValueError("requests must be at least a second apart")
        self._client = client
        self._sleeper = sleeper
        self._gap = seconds_between_requests
        self._requests = 0

    @property
    def requests(self) -> int:
        return self._requests

    async def get(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        if self._requests:
            await self._sleeper.sleep(self._gap)
        self._requests += 1
        response = await self._client.get(
            url, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        if response.status_code in (401, 403, 429):
            raise SourceRefused(f"{url}: answered {response.status_code}; stopping the run")
        if response.status_code == 404:
            raise NotFound(url)
        response.raise_for_status()
        return response.content
