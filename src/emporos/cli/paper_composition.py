"""Composition root for paper trading: the only place the paper broker's collaborators are chosen.

    market data (a real source)  ──▶  PaperBroker  ──▶  MongoPaperJournal  ──▶  Atlas
                                          ▲
                        fee schedule, fill policies, session restored from the store

The market-data source is any object with the market-data half of a broker. `live_market_data`
shows the real one — the Angel One adapter, which satisfies the Protocol structurally (mypy checks
it) — and is the ONLY thing paper trading takes from Angel One: never an order endpoint, and the
paper broker holds no credentials. Strategies, risk and execution receive the result as a plain
`Broker`.
"""

from __future__ import annotations

from dataclasses import dataclass

from emporos.broker.angelone.adapter import AngelOneBroker
from emporos.broker.paper.broker import PaperBroker
from emporos.broker.paper.factory import PaperBrokerFactory
from emporos.broker.paper.flusher import JournalFlusher
from emporos.broker.paper.journal import PaperJournal, PaperSessionStore
from emporos.broker.paper.market import MarketDataSource
from emporos.core.alerts import AlertSink
from emporos.core.clock import IST, Clock, Sleeper
from emporos.core.ids import IdGenerator


def live_market_data(broker: AngelOneBroker) -> MarketDataSource:
    """The live Angel One adapter, seen through the narrow market-data Protocol only."""
    return broker


@dataclass(frozen=True)
class PaperRuntime:
    """A running paper session: the broker to hand to strategies, and the task that keeps its
    journal durable (run it as a background task; cancelling it flushes one last time)."""

    broker: PaperBroker
    flusher: JournalFlusher


@dataclass(frozen=True, kw_only=True)
class PaperComposer:
    source: MarketDataSource
    factory: PaperBrokerFactory
    journal: PaperJournal
    store: PaperSessionStore
    client_code: str
    clock: Clock
    ids: IdGenerator
    sleeper: Sleeper
    alerts: AlertSink
    flush_interval_seconds: float = 1.0

    async def open(self) -> PaperRuntime:
        """Start today's session, resuming whatever the store already holds for this account."""
        session_date = self.clock.now().astimezone(IST).date().isoformat()
        restored = await self.store.load(self.client_code, session_date)
        broker = self.factory.build(self.source, self.journal, self.clock, self.ids, restored)
        flusher = JournalFlusher(
            self.journal, self.sleeper, self.alerts, self.flush_interval_seconds
        )
        return PaperRuntime(broker, flusher)
