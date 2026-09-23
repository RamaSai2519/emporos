"""Shared candle-building helper for the research package's tests (EM-178)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money

START = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)  # 09:15 IST


def bar(
    index: int,
    close: Decimal | int,
    *,
    instrument_id: str = "NSE:1",
    timeframe: Timeframe = Timeframe.M5,
    open_: Decimal | int | None = None,
    high: Decimal | int | None = None,
    low: Decimal | int | None = None,
    volume: int = 1000,
) -> Candle:
    close_d = Decimal(close)
    open_d = Decimal(open_) if open_ is not None else close_d
    high_d = Decimal(high) if high is not None else max(open_d, close_d)
    low_d = Decimal(low) if low is not None else min(open_d, close_d)
    return Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        ts=START + index * timeframe.duration,
        open=Money.of(open_d),
        high=Money.of(high_d),
        low=Money.of(low_d),
        close=Money.of(close_d),
        volume=volume,
    )


def bars(closes: list[Decimal | int], **kwargs: object) -> list[Candle]:
    return [bar(i, c, **kwargs) for i, c in enumerate(closes)]  # type: ignore[arg-type]
