"""Rate-limit decorator over any `RestTransport` (EM-44)."""

from __future__ import annotations

from typing import Any

from emporos.broker.angelone.transport import RestRequest, RestTransport
from emporos.broker.ratelimit import RateLimiter


class RateLimitedTransport:
    """Waits for the request's endpoint group to have capacity, then delegates.

    Sits *under* the retry layer, so every retry attempt is throttled too."""

    def __init__(self, inner: RestTransport, limiter: RateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter

    async def send(self, request: RestRequest) -> Any:
        await self._limiter.acquire(request.endpoint.group.value)
        return await self._inner.send(request)
