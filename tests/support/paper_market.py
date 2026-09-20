"""A `MarketDataSource` for tests: static reference data and quotes, hand-driven ticks.

Ticks fan out through the REAL `TickBroadcaster`, the same object the live pipeline uses."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import CandleRequest, MarketDataMode, Quote
from emporos.domain.candles import Candle
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.domain.ticks import Tick
from emporos.marketdata.broadcast import TickBroadcaster

NOW = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)  # 09:30 IST on a Friday


class FakeMarketData:
    def __init__(self, instruments: Sequence[Instrument], history: list[Candle]) -> None:
        self._instruments = {i.instrument_id: i for i in instruments}
        self._history = history
        self._ticks = TickBroadcaster()
        self.subscribed: set[str] = set()

    def emit(self, tick: Tick) -> None:
        self._ticks.on_tick(tick)

    async def get_instruments(self) -> Sequence[Instrument]:
        return list(self._instruments.values())

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        self._require(instrument_ids)
        p = Money.of("100.00")
        return [
            Quote(
                i,
                p,
                p,
                p,
                p,
                p,
                10,
                NOW,
                Money.of("90"),
                Money.of("110"),
                bid=Money.of("99.95"),
                ask=Money.of("100.05"),
            )  # fmt: skip
            for i in instrument_ids
        ]

    async def get_historical_candles(self, request: CandleRequest) -> list[Candle]:
        self._require([request.instrument_id])
        bars = {
            c.ts: c
            for c in self._history
            if c.instrument_id == request.instrument_id
            and c.timeframe is request.timeframe
            and request.start <= c.ts < request.end
        }
        return [bars[ts] for ts in sorted(bars)]

    async def subscribe_market_data(
        self, instrument_ids: Sequence[str], mode: MarketDataMode
    ) -> None:
        self._require(instrument_ids)
        self.subscribed.update(instrument_ids)

    async def unsubscribe_market_data(self, instrument_ids: Sequence[str]) -> None:
        self._require(instrument_ids)
        self.subscribed.difference_update(instrument_ids)

    def on_tick(self, handler: Callable[[Tick], None]) -> None:
        self._ticks.add_handler(handler)

    def _require(self, instrument_ids: Sequence[str]) -> None:
        for instrument_id in instrument_ids:
            if instrument_id not in self._instruments:
                raise BrokerRejectedError(f"unknown instrument {instrument_id!r}")
