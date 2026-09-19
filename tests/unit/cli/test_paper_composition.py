"""The paper composition root: it resumes today's session and hands back a plain `Broker`."""

from __future__ import annotations

from emporos.broker.base import Broker
from emporos.broker.paper.costs import NoCosts
from emporos.broker.paper.exchange import RestoredOrder
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.journal import RestoredSession
from emporos.cli.paper_composition import PaperComposer
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from tests.support.paper_journal import RecordingJournal
from tests.support.paper_market import NOW, FakeMarketData
from tests.support.paper_rig import SBIN, order


class Store:
    def __init__(self, restored: RestoredSession) -> None:
        self.restored = restored
        self.asked: list[tuple[str, str]] = []

    async def load(self, account_id: str, session_date: str) -> RestoredSession:
        self.asked.append((account_id, session_date))
        return self.restored


def composer(store: Store) -> PaperComposer:
    return PaperComposer(
        source=FakeMarketData([SBIN], []),
        factory=PaperBrokerFactory(PaperBrokerConfig("PAPER01", Money.of("1000")), NoCosts()),
        journal=RecordingJournal(),
        store=store,
        client_code="PAPER01",
        clock=FixedClock(NOW),
        ids=IdGenerator(),
        sleeper=AsyncioSleeper(),
        alerts=LogAlertSink(),
    )


async def test_it_opens_todays_session_for_the_account_and_returns_a_plain_broker() -> None:
    store = Store(RestoredSession([], []))

    runtime = await composer(store).open()

    assert isinstance(runtime.broker, Broker)
    assert store.asked == [("PAPER01", "2026-09-18")]  # the IST session date of the clock
    await runtime.broker.place_order(order("CMP001"))
    assert len(await runtime.broker.get_order_book()) == 1


async def test_it_resumes_what_the_store_holds() -> None:
    first = await composer(Store(RestoredSession([], []))).open()
    await first.broker.place_order(order("CMP002"))
    (placed,) = await first.broker.get_order_book()

    resumed = await composer(Store(RestoredSession([RestoredOrder(placed, 1, 0)], []))).open()

    assert await resumed.broker.get_order_book() == [placed]
