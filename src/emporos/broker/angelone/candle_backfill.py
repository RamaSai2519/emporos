"""Broker-history backfill for reconnect recovery: `getCandleData` -> domain 1m `Candle`s.

Implements the `CandleBackfillSource` the marketdata layer defines, without marketdata importing
this package. Rate limiting, the spurious-denial backoff and session handling all come from the
transport stack underneath `AngelOneApi`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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


@dataclass(frozen=True)
class RejectedBar:
    """A bar the broker sent that is not a valid candle (e.g. its high is below its low)."""

    instrument_id: str
    timeframe: Timeframe
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def describe(self) -> str:
        return (
            f"{self.instrument_id} {self.timeframe.value} {self.ts.isoformat()} "
            f"o={self.open} h={self.high} l={self.low} c={self.close}"
        )


class BarRejections:
    """Collects the bars an adapter dropped, so a report can say exactly which and how many."""

    def __init__(self) -> None:
        self.bars: list[RejectedBar] = []

    def __call__(self, bar: RejectedBar) -> None:
        self.bars.append(bar)


class AngelOneCandleBackfill:
    """Serves both reconnect recovery (`fetch_minutes`) and historical backfill (`fetch`).

    By default an inconsistent bar fails the whole request (a typed error): recovering a live gap
    from a broken response is not something to do quietly. A historical backfill instead passes
    `on_rejected`: the bad bar is handed to it and skipped, so one defective minute does not cost
    the 28 days requested with it.
    """

    def __init__(
        self, api: AngelOneApi, on_rejected: Callable[[RejectedBar], None] | None = None
    ) -> None:
        self._api = api
        self._on_rejected = on_rejected

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
        candles: list[Candle] = []
        for bar in bars:
            try:
                candles.append(
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
                )
            except ValueError:  # e.g. the broker sent a bar whose high is below its low
                if self._on_rejected is None:
                    raise BrokerProtocolError(
                        f"{Endpoints.CANDLES.name}: an inconsistent candle for "
                        f"{instrument.instrument_id}"
                    ) from None
                self._on_rejected(
                    RejectedBar(
                        instrument.instrument_id,
                        timeframe,
                        bar.ts,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                    )  # fmt: skip
                )
        return candles
