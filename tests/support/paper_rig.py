"""A ready-wired `PaperBroker` over a hand-driven market, with knobs for every policy."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import count

from emporos.broker.models import BrokerTrade, PlaceOrderRequest
from emporos.broker.paper.broker import PaperBroker
from emporos.broker.paper.costs import CostModel, NoCosts
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.faults import ReplyLoss
from emporos.broker.paper.fills import (
    AtLimitPrice,
    FillPolicy,
    LimitFillPolicy,
    LiquidityModel,
    TouchCrossing,
)
from emporos.broker.paper.journal import RestoredSession
from emporos.broker.paper.rejects import OrderScreen
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from tests.support.fakes import make_instrument, make_tick
from tests.support.paper_journal import RecordingJournal
from tests.support.paper_market import NOW, FakeMarketData

SBIN = make_instrument("3045", symbol="SBIN-EQ")
ID = SBIN.instrument_id


class FlatCosts:
    """A fixed charge per trade: makes fee arithmetic checkable by eye."""

    def __init__(self, per_trade: str) -> None:
        self._per_trade = Money.of(per_trade)

    def charges(self, trade: BrokerTrade) -> Money:
        return self._per_trade


class PaperRig:
    def __init__(
        self,
        broker: PaperBroker,
        market: FakeMarketData,
        clock: FixedClock,
        journal: RecordingJournal,
    ) -> None:
        self.broker = broker
        self.market = market
        self.clock = clock
        self.journal = journal
        self._volume = 1_000_000
        self._sequence = count(1)

    def tape(self, price: str, shares: int = 0, *, out_of_order: bool = False) -> None:
        """One tick at `price` in which `shares` traded since the previous tick."""
        self._volume += shares
        self.market.emit(
            make_tick(
                self.clock.now(), price, volume=self._volume, instrument_id=ID,
                sequence=next(self._sequence), out_of_order=out_of_order,
            )
        )  # fmt: skip

    def advance(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))


def build_rig(
    *,
    cash: str = "1000000",
    leverage: str = "1",
    latency: timedelta = timedelta(0),
    costs: CostModel | None = None,
    fill_policy: FillPolicy | None = None,
    liquidity: LiquidityModel | None = None,
    screen: OrderScreen | None = None,
    replies: ReplyLoss | None = None,
    journal: RecordingJournal | None = None,
    restored: RestoredSession | None = None,
    baseline: bool = True,
) -> PaperRig:
    clock = FixedClock(NOW)
    market = FakeMarketData([SBIN], [])
    journal = journal or RecordingJournal()
    factory = PaperBrokerFactory(
        PaperBrokerConfig("PAPER01", Money.of(cash), Decimal(leverage), latency),
        costs or NoCosts(),
        fill_policy=fill_policy or LimitFillPolicy(TouchCrossing(), AtLimitPrice()),
        liquidity=liquidity,
        screen=screen,
        replies=replies,
    )
    rig = PaperRig(
        factory.build(market, journal, clock, IdGenerator(), restored), market, clock, journal
    )
    if baseline:
        rig.tape("100.00")  # a volume counter needs a first reading before it can measure
    return rig


def order(
    tag: str = "TAG001",
    side: OrderSide = OrderSide.BUY,
    qty: int = 10,
    price: str = "100.00",
    **kw: object,
) -> PlaceOrderRequest:
    return PlaceOrderRequest(ID, side, OrderType.LIMIT, qty, Money.of(price), tag, **kw)  # type: ignore[arg-type]
