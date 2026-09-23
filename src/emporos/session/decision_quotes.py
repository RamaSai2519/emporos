"""Remembers the market each risk review actually used (EM-185), without a second quote fetch.

`QuoteCapturingMarketFacts` wraps the `MarketFactsSource` the risk snapshot is built from and
notes what it returned; `GatedExecutionSink` then stamps that onto the signal's record. Because it
is the SAME answer risk judged, a later paper-vs-backtest comparison measures slippage against the
price the system truly saw.
"""

from __future__ import annotations

from emporos.core.clock import Clock
from emporos.risk.assembly import MarketFactsSource
from emporos.risk.snapshot import InstrumentMarket
from emporos.signals.quotes import DecisionQuote


class QuoteCapturingMarketFacts:
    def __init__(self, inner: MarketFactsSource, clock: Clock, source: str) -> None:
        self._inner = inner
        self._clock = clock
        self._source = source
        self._latest: dict[str, DecisionQuote] = {}

    async def market_facts(self, instrument_id: str) -> InstrumentMarket:
        market = await self._inner.market_facts(instrument_id)
        if market.ltp is not None or market.bid is not None or market.ask is not None:
            self._latest[instrument_id] = DecisionQuote(
                self._clock.now(), self._source, market.ltp, market.bid, market.ask
            )
        return market

    def take(self, instrument_id: str) -> DecisionQuote | None:
        return self._latest.pop(instrument_id, None)
