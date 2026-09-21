"""The market-data half of a broker: the only thing `PaperBroker` takes from the real world.

`PaperBroker` never sees the order methods of whatever supplies its data — this Protocol has
none — so simulated trading cannot reach a broker's order endpoints, by construction. The
composition root passes in any object with these methods (the live Angel One adapter satisfies
it structurally). Whoever owns the source owns its session and its credentials; the paper broker
holds none.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from emporos.broker.models import CandleRequest, MarketDataMode, Quote
from emporos.domain.candles import Candle
from emporos.domain.instruments import Instrument
from emporos.domain.ticks import Tick


class MarketDataSource(Protocol):
    async def get_instruments(self) -> Sequence[Instrument]: ...

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]: ...

    async def get_historical_candles(self, request: CandleRequest) -> list[Candle]: ...

    async def subscribe_market_data(
        self, instrument_ids: Sequence[str], mode: MarketDataMode
    ) -> None: ...

    async def unsubscribe_market_data(self, instrument_ids: Sequence[str]) -> None: ...

    def on_tick(self, handler: Callable[[Tick], None]) -> None: ...


class MarketDataOnly:
    """Narrows any broker to the six market-data calls, and nothing else.

    The Protocol above already keeps `PaperBroker` from calling an order method; this makes the
    object it is handed unable to, too: it holds the wider broker privately and forwards exactly
    these calls, so `place_order` is not reachable from anything built on the paper path.
    """

    def __init__(self, source: MarketDataSource) -> None:
        self._source = source

    async def get_instruments(self) -> Sequence[Instrument]:
        return await self._source.get_instruments()

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        return await self._source.get_quote(instrument_ids)

    async def get_historical_candles(self, request: CandleRequest) -> list[Candle]:
        return await self._source.get_historical_candles(request)

    async def subscribe_market_data(
        self, instrument_ids: Sequence[str], mode: MarketDataMode
    ) -> None:
        await self._source.subscribe_market_data(instrument_ids, mode)

    async def unsubscribe_market_data(self, instrument_ids: Sequence[str]) -> None:
        await self._source.unsubscribe_market_data(instrument_ids)

    def on_tick(self, handler: Callable[[Tick], None]) -> None:
        self._source.on_tick(handler)
