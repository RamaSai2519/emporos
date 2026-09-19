"""Broker-history backfill for reconnect recovery: `getCandleData` -> domain 1m `Candle`s.

Implements the `CandleBackfillSource` the marketdata layer defines, without marketdata importing
this package. Rate limiting, the spurious-denial backoff and session handling all come from the
transport stack underneath `AngelOneApi`.
"""

from __future__ import annotations

from datetime import datetime

from emporos.broker.angelone.api import AngelOneApi, CandleInterval
from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.errors import BrokerProtocolError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money

_INTERVAL = {
    Timeframe.M1: CandleInterval.ONE_MINUTE,
    Timeframe.M5: CandleInterval.FIVE_MINUTE,
    Timeframe.M15: CandleInterval.FIFTEEN_MINUTE,
    Timeframe.H1: CandleInterval.ONE_HOUR,
    Timeframe.D1: CandleInterval.ONE_DAY,
}


class AngelOneCandleBackfill:
    """Serves both reconnect recovery (`fetch_minutes`) and historical backfill (`fetch`)."""

    def __init__(self, api: AngelOneApi) -> None:
        self._api = api

    async def fetch_minutes(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> list[Candle]:
        return await self.fetch(instrument, Timeframe.M1, start, end)

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        bars = await self._api.candles(
            instrument.exchange.value, instrument.token, _INTERVAL[timeframe], start, end
        )
        try:
            return [
                Candle(
                    instrument_id=instrument.instrument_id,
                    timeframe=timeframe,
                    ts=bar.ts,
                    open=Money(bar.open),
                    high=Money(bar.high),
                    low=Money(bar.low),
                    close=Money(bar.close),
                    volume=bar.volume,
                )
                for bar in bars
            ]
        except ValueError:  # e.g. the broker sent a bar whose high is below its low
            raise BrokerProtocolError(
                f"{Endpoints.CANDLES.name}: an inconsistent candle for {instrument.instrument_id}"
            ) from None
