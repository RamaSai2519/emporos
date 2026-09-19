"""The watchlist's live token subscriptions (EM-50).

Angel One allows at most 1000 tokens per connection and 3 connections per client code (plan.md
§1.5); v1 uses exactly one connection. plan.md §7 sizes the pipeline for <= 200 tokens, so 200 is
the default operating cap and 1000 is a ceiling the cap can never exceed. A batch that would
breach the cap is rejected whole with a clear error — never silently truncated.

The manager owns the *desired* set. A reconnect (or a subscribe issued while the socket is down)
is repaired by resending that whole set from `on_connected`, so the live subscription always
converges on the desired one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol

from emporos.core.errors import ConfigurationError, DefinitiveError
from emporos.domain.instruments import Instrument

_LOG = logging.getLogger(__name__)


class SubscriptionTransport(Protocol):
    """What the manager needs from a feed connection. Implemented by the broker adapter."""

    async def subscribe(self, instruments: Sequence[Instrument]) -> None: ...

    async def unsubscribe(self, instruments: Sequence[Instrument]) -> None: ...


@dataclass(frozen=True)
class SubscriptionLimits:
    API_CEILING: ClassVar[int] = 1000  # SmartAPI's documented per-connection maximum
    DEFAULT_CAP: ClassVar[int] = 200  # plan.md §7's operating cap

    max_tokens: int = DEFAULT_CAP

    def __post_init__(self) -> None:
        if not 1 <= self.max_tokens <= self.API_CEILING:
            raise ConfigurationError(
                f"max_tokens must be between 1 and {self.API_CEILING}, got {self.max_tokens}"
            )


class SubscriptionLimitExceededError(DefinitiveError):
    """The batch would exceed the connection's token cap. Nothing was subscribed."""

    def __init__(self, *, requested_new: int, active: int, limit: int) -> None:
        super().__init__(
            f"cannot subscribe {requested_new} more instrument(s): {active} already active and the "
            f"limit is {limit} per connection"
        )
        self.requested_new = requested_new
        self.active = active
        self.limit = limit


class SubscriptionManager:
    """Also a `ConnectionListener`: hand it to the feed client and it restores the watchlist."""

    def __init__(
        self, transport: SubscriptionTransport, limits: SubscriptionLimits | None = None
    ) -> None:
        self._transport = transport
        self._limits = limits or SubscriptionLimits()
        self._desired: dict[str, Instrument] = {}
        self._connected = False
        self._lock = asyncio.Lock()

    @property
    def active(self) -> tuple[Instrument, ...]:
        return tuple(self._desired.values())

    @property
    def limit(self) -> int:
        return self._limits.max_tokens

    def is_subscribed(self, instrument_id: str) -> bool:
        return instrument_id in self._desired

    async def subscribe(self, instruments: Iterable[Instrument]) -> None:
        batch = {i.instrument_id: i for i in instruments}
        async with self._lock:
            new = [i for key, i in batch.items() if key not in self._desired]
            if len(self._desired) + len(new) > self._limits.max_tokens:
                raise SubscriptionLimitExceededError(
                    requested_new=len(new), active=len(self._desired), limit=self._limits.max_tokens
                )
            for instrument in new:
                self._desired[instrument.instrument_id] = instrument
            if new and self._connected:
                await self._transport.subscribe(new)

    async def unsubscribe(self, instruments: Iterable[Instrument]) -> None:
        async with self._lock:
            gone = [i for i in instruments if self._desired.pop(i.instrument_id, None) is not None]
            if gone and self._connected:
                await self._transport.unsubscribe(gone)

    async def on_connected(self) -> None:
        async with self._lock:
            self._connected = True
            if self._desired:
                _LOG.info("feed connected; resubscribing %d instrument(s)", len(self._desired))
                await self._transport.subscribe(tuple(self._desired.values()))

    async def on_disconnected(self, reason: str) -> None:
        async with self._lock:
            self._connected = False
            _LOG.warning(
                "feed disconnected (%s); %d instrument(s) pending", reason, len(self._desired)
            )
