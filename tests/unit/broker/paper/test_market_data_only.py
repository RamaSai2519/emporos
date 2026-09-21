"""`MarketDataOnly` is what a paper worker holds in place of a live broker: it forwards the six
market-data calls and has nothing else, so an order endpoint is not reachable through it."""

from __future__ import annotations

from datetime import timedelta

from emporos.broker.models import CandleRequest, MarketDataMode
from emporos.broker.paper.market import MarketDataOnly
from emporos.domain.candles import Timeframe
from emporos.domain.ticks import Tick
from tests.support.paper_market import NOW, FakeMarketData
from tests.support.paper_rig import ID, SBIN


class WideBroker(FakeMarketData):
    """A source that ALSO has order methods, as the real Angel One broker does."""

    def __init__(self) -> None:
        super().__init__([SBIN], [])
        self.orders_placed = 0

    async def place_order(self) -> None:
        self.orders_placed += 1


async def test_the_market_data_calls_are_forwarded() -> None:
    inner = WideBroker()
    narrow = MarketDataOnly(inner)
    heard: list[Tick] = []

    assert [i.instrument_id for i in await narrow.get_instruments()] == [ID]
    assert [q.instrument_id for q in await narrow.get_quote([ID])] == [ID]
    await narrow.subscribe_market_data([ID], MarketDataMode.QUOTE)
    assert inner.subscribed == {ID}
    await narrow.unsubscribe_market_data([ID])
    assert inner.subscribed == set()
    request = CandleRequest(ID, Timeframe.M5, NOW, NOW + timedelta(hours=1))
    assert await narrow.get_historical_candles(request) == []
    narrow.on_tick(heard.append)
    assert heard == []  # registered, and nothing was emitted


def test_an_order_method_is_not_reachable_through_it() -> None:
    narrow = MarketDataOnly(WideBroker())

    public = {name for name in dir(narrow) if not name.startswith("_")}

    assert public == {
        "get_instruments",
        "get_quote",
        "get_historical_candles",
        "subscribe_market_data",
        "unsubscribe_market_data",
        "on_tick",
    }
    assert not hasattr(narrow, "place_order")
    assert not hasattr(narrow, "cancel_order")
