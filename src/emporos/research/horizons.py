"""Forward returns over intraday horizons (EM-178), from whole numbers of a study's base
timeframe bars.

`Timeframe` (`emporos.domain.candles`) only has native 1m/5m/15m/1h/1d bars; a 30m or 60m horizon
is expressed as a whole number of the STUDY's base timeframe (e.g. six 5m bars = 30m), never
resampled — resampling would blur exactly which closed bar the horizon lands on, and this project
keeps timestamps exact (plan.md §6). A horizon that does not divide evenly into the base timeframe
is refused at construction, not silently rounded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.candles import Candle, Timeframe

STANDARD_HORIZONS: tuple[timedelta, ...] = (
    timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30), timedelta(minutes=60),
)  # fmt: skip


@dataclass(frozen=True)
class Horizon:
    span: timedelta
    bars: int  # how many base-timeframe bars ahead this horizon closes at

    @property
    def label(self) -> str:
        minutes = int(self.span.total_seconds() // 60)
        return f"{minutes}m"


class ForwardReturnCalculator:
    """Simple forward return from the close AT a bar to the close `horizon.bars` ahead of it, for
    each horizon, over one base timeframe."""

    def __init__(
        self, base_timeframe: Timeframe, horizons: Sequence[timedelta] = STANDARD_HORIZONS
    ) -> None:
        if not horizons:
            raise ValueError("a calculator needs at least one horizon")
        self._base = base_timeframe
        self._horizons = tuple(self._resolve(span) for span in horizons)

    @property
    def horizons(self) -> tuple[Horizon, ...]:
        return self._horizons

    def _resolve(self, span: timedelta) -> Horizon:
        base_seconds = Decimal(self._base.duration.total_seconds())
        bars, remainder = divmod(Decimal(span.total_seconds()), base_seconds)
        if remainder or bars < 1:
            raise ValueError(f"{span} is not a whole number of {self._base.value} bars")
        return Horizon(span, int(bars))

    def returns_at(self, bars: Sequence[Candle], index: int) -> dict[Horizon, Decimal | None]:
        """Forward return from `bars[index]`'s close to each horizon's close; `None` for a
        horizon that runs past the end of the series."""
        origin = bars[index].close.amount
        result: dict[Horizon, Decimal | None] = {}
        for horizon in self._horizons:
            target = index + horizon.bars
            if target >= len(bars) or origin == Decimal(0):
                result[horizon] = None
            else:
                result[horizon] = DecimalMath.divide(bars[target].close.amount - origin, origin)
        return result
