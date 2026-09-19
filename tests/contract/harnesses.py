"""One harness per `Broker` implementation. The contract suite (`test_broker_contract.py`) runs
every test against every harness registered in `HARNESS_FACTORIES`.

Adding an implementation (PaperBroker in Phase 8, SimulatedBroker later) means writing one factory
here — the suite itself does not change, which is exactly what makes it a contract."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from emporos.broker.angelone.adapter import AngelOneBroker
from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.mapping import AccountMapper
from emporos.broker.angelone.models import OrderBookEntry
from emporos.broker.angelone.ws_orders import OrderUpdateHub
from emporos.broker.base import Broker
from emporos.broker.models import BrokerOrderUpdate
from emporos.broker.paper.costs import NoCosts
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.faults import ScriptedReplyLoss
from emporos.broker.paper.fills import (
    AtTradePrice,
    LimitFillPolicy,
    ParticipationLiquidity,
    TouchCrossing,
)
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
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
from tests.support.fakes import make_instrument, make_tick
from tests.support.in_memory_broker import InMemoryBroker
from tests.support.paper_journal import RecordingJournal
from tests.support.paper_market import NOW as PAPER_NOW
from tests.support.paper_market import FakeMarketData

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


def paper() -> BrokerHarness:
    """`PaperBroker` over a hand-driven market: an order fills when a tick that trades enough
    volume crosses its limit, exactly as it would on live data. The exchange-fill hook plays the
    tape: a tick at the fill price whose traded volume equals the fill quantity, so a 100%
    participation model turns it into exactly that fill."""
    history: list[Candle] = []
    clock = FixedClock(PAPER_NOW)
    market = FakeMarketData([SBIN, RELIANCE], history)
    replies = ScriptedReplyLoss()
    factory = PaperBrokerFactory(
        PaperBrokerConfig("PAPER01", Money.of("1000000")),
        NoCosts(),
        fill_policy=LimitFillPolicy(TouchCrossing(), AtTradePrice()),
        liquidity=ParticipationLiquidity(Decimal(1)),
        replies=replies,
    )
    broker = factory.build(market, RecordingJournal(), clock, IdGenerator())
    sequence = itertools.count(1)
    traded = [1_000_000]

    def emit(tick: Tick) -> None:
        market.emit(tick)

    def tape(price: str, shares: int) -> None:
        traded[0] += shares
        market.emit(
            make_tick(
                clock.now(), price, volume=traded[0], instrument_id=SBIN.instrument_id,
                sequence=next(sequence) + 1000,
            )
        )  # fmt: skip

    tape("1000.00", 0)  # the baseline tick: a volume counter needs a first reading

    def fill(order_id: str, quantity: int, price: Money) -> None:
        tape(str(price.amount), quantity)

    return BrokerHarness("paper", broker, SBIN, history, emit, fill, replies.lose_next)


HARNESS_FACTORIES: dict[str, Callable[[], BrokerHarness]] = {
    "in_memory": in_memory,
    "angelone": angelone,
    "paper": paper,
}
