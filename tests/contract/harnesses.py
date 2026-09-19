"""One harness per `Broker` implementation. The contract suite (`test_broker_contract.py`) runs
every test against every harness registered in `HARNESS_FACTORIES`.

Adding an implementation (PaperBroker in Phase 8, SimulatedBroker later) means writing one factory
here — the suite itself does not change, which is exactly what makes it a contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from emporos.broker.angelone.adapter import AngelOneBroker
from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.mapping import AccountMapper
from emporos.broker.angelone.models import OrderBookEntry
from emporos.broker.angelone.ws_orders import OrderUpdateHub
from emporos.broker.base import Broker
from emporos.broker.models import BrokerOrderUpdate
from emporos.domain.candles import Candle
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.domain.ticks import Tick
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.broadcast import TickBroadcaster
from tests.support.angelone_broker import (
    NOW,
    RecordingCandleFetcher,
    RecordingMarketData,
    StubCatalog,
    StubSessions,
)
from tests.support.fake_smartapi import FakeSmartApi
from tests.support.fakes import make_instrument
from tests.support.in_memory_broker import InMemoryBroker

SBIN = make_instrument("3045", symbol="SBIN-EQ")
RELIANCE = make_instrument("2885", symbol="RELIANCE-EQ")


@dataclass
class BrokerHarness:
    """A broker under test plus the hooks the contract needs to drive its world."""

    name: str
    broker: Broker
    instrument: Instrument
    history: list[Candle]  # seed 1m bars here; `get_historical_candles` serves them
    emit_tick: Callable[[Tick], None]
    fill: Callable[[str, int, Money], None]  # the exchange fills an order
    lose_next_place_reply: Callable[[], None]  # the next place succeeds but its reply is lost


def in_memory() -> BrokerHarness:
    history: list[Candle] = []
    broker = InMemoryBroker([SBIN, RELIANCE], history)

    def lose() -> None:
        broker.lose_next_place_reply = True

    return BrokerHarness("in_memory", broker, SBIN, history, broker.emit_tick, broker.fill, lose)


def angelone() -> BrokerHarness:
    """`AngelOneBroker` over a stateful SmartAPI emulator: request bodies go in, wire-format order
    books come out, and the adapter must round-trip them."""
    history: list[Candle] = []
    smartapi = FakeSmartApi({"3045": "SBIN-EQ", "2885": "RELIANCE-EQ"})
    ticks, updates = (
        TickBroadcaster(),
        OrderUpdateHub(),
    )  # the REAL fan-out, failure isolation included
    mapper = AccountMapper()

    def fill(order_id: str, quantity: int, price: Money) -> None:
        smartapi.fill(order_id, quantity, price.amount)
        row = smartapi.orders[order_id]
        order = mapper.order(OrderBookEntry.model_validate(row))
        updates.publish(BrokerOrderUpdate(order, NOW))

    def lose() -> None:
        smartapi.lose_next_place_reply = True

    broker = AngelOneBroker(
        AngelOneApi(smartapi),
        StubSessions(),
        "A0000000",
        InstrumentCache([SBIN, RELIANCE]),
        StubCatalog([SBIN, RELIANCE]),
        RecordingCandleFetcher(
            lambda inst, s, e: [c for c in history if c.instrument_id == inst.instrument_id]
        ),
        RecordingMarketData(),
        ticks,
        updates,
    )
    return BrokerHarness("angelone", broker, SBIN, history, ticks.on_tick, fill, lose)


HARNESS_FACTORIES: dict[str, Callable[[], BrokerHarness]] = {
    "in_memory": in_memory,
    "angelone": angelone,
}
