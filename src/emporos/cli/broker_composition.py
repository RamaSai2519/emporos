"""Composition root for `AngelOneBroker`: the only place its collaborators are chosen.

Given the already-built pieces (transport stack, market-data stack, order-update hub), it wires the
adapter. Strategies, risk and execution receive the result as a plain `Broker` and never see any
of this.
"""

from __future__ import annotations

from dataclasses import dataclass

from emporos.broker.angelone.adapter import (
    AngelOneBroker,
    CandleFetcher,
    InstrumentCatalog,
    MarketDataControl,
    SessionService,
)
from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.ws_orders import OrderUpdateHub
from emporos.domain.instruments import InstrumentResolver
from emporos.marketdata.broadcast import TickBroadcaster


@dataclass(frozen=True, kw_only=True)
class BrokerComposer:
    api: AngelOneApi
    sessions: SessionService
    client_code: str
    resolver: InstrumentResolver
    catalog: InstrumentCatalog
    candles: CandleFetcher
    market_data: MarketDataControl
    ticks: TickBroadcaster
    order_updates: OrderUpdateHub

    def build(self) -> AngelOneBroker:
        return AngelOneBroker(
            self.api,
            self.sessions,
            self.client_code,
            self.resolver,
            self.catalog,
            self.candles,
            self.market_data,
            self.ticks,
            self.order_updates,
        )
