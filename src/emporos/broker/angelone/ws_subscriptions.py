"""Adapts the market-feed client to the `SubscriptionTransport` the marketdata layer expects."""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence

from emporos.broker.angelone.ws_market import (
    ExchangeType,
    FeedMode,
    MarketFeedClient,
    SubscriptionAction,
)
from emporos.domain.instruments import Instrument

_TOKENS_PER_MESSAGE = 100


def _sequential_correlation_ids() -> Callable[[], str]:
    """Angel One echoes a client-chosen 10-character id back in error replies."""
    counter = itertools.count(1)
    return lambda: f"{next(counter):010d}"


class FeedSubscriptionAdapter:
    def __init__(
        self,
        client: MarketFeedClient,
        mode: FeedMode = FeedMode.QUOTE,
        correlation_ids: Callable[[], str] | None = None,
    ) -> None:
        self._client = client
        self._mode = mode
        self._next_id = correlation_ids or _sequential_correlation_ids()

    async def subscribe(self, instruments: Sequence[Instrument]) -> None:
        await self._send(SubscriptionAction.SUBSCRIBE, instruments)

    async def unsubscribe(self, instruments: Sequence[Instrument]) -> None:
        await self._send(SubscriptionAction.UNSUBSCRIBE, instruments)

    async def _send(self, action: SubscriptionAction, instruments: Sequence[Instrument]) -> None:
        by_exchange: dict[ExchangeType, list[str]] = defaultdict(list)
        for instrument in instruments:
            by_exchange[ExchangeType.of(instrument.exchange)].append(instrument.token)
        for exchange, tokens in by_exchange.items():
            for chunk in _chunks(tokens, _TOKENS_PER_MESSAGE):
                await self._client.send_subscription(
                    action, self._mode, {exchange: chunk}, self._next_id()
                )


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
