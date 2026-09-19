from __future__ import annotations

from datetime import UTC, datetime, timedelta

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money


def make_candle(
    instrument_id: str, ts: datetime, close: str = "100.5", partial: bool = False
) -> Candle:
    price = Money.of(close)
    return Candle(
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        ts=ts,
        open=price,
        high=price + Money.of("1"),
        low=price - Money.of("1"),
        close=price,
        volume=100,
        partial=partial,
    )


def minute_series(
    instrument_id: str, start: datetime, count: int, every: timedelta = timedelta(minutes=1)
) -> list[Candle]:
    return [
        make_candle(instrument_id, start + every * i, close=f"{100 + i}.05") for i in range(count)
    ]


def at(year: int, month: int, day: int, hour: int = 4, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)
