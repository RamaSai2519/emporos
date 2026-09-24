"""rsi_pullback_v1 — buy a sharp pullback in an uptrend (sell a sharp rally in a downtrend).

The trend is the side of a slow EMA the price is on. A very short RSI (`rsi_period`, default 2)
at an extreme against the trend means a stretched pullback: buy when price is above the EMA and RSI
is at or below `rsi_entry`; sell short when price is below the EMA and RSI is at or above
`100 - rsi_entry`. The trade exits when RSI recovers to `rsi_exit` (or `100 - rsi_exit` for a
short), when price moves `stop_atr_mult` ATRs against the entry, or after `max_hold_bars` bars.
No entries in the first `skip_bars` of the day or after `session.no_new_entries_after`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from pydantic import model_validator

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.strategies.config import (
    ExactDecimal,
    NonNegativeInt,
    PositiveInt,
    ResolvedStrategyConfig,
    StrategyParameters,
)
from emporos.strategies.indicators import (
    AverageTrueRange,
    ExponentialMovingAverage,
    RelativeStrengthIndex,
)
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy


class RsiPullbackParameters(StrategyParameters):
    trend_ema: PositiveInt = 100
    rsi_period: PositiveInt = 2
    rsi_entry: ExactDecimal = Decimal(10)
    rsi_exit: ExactDecimal = Decimal(60)
    atr_period: PositiveInt = 14
    stop_atr_mult: ExactDecimal = Decimal(2)
    max_hold_bars: PositiveInt = 24
    skip_bars: NonNegativeInt = 8
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> RsiPullbackParameters:
        if not 0 < self.rsi_entry < 50 or not 50 <= self.rsi_exit < 100:
            raise ValueError("need 0 < rsi_entry < 50 <= rsi_exit < 100")
        if self.stop_atr_mult <= 0:
            raise ValueError("stop_atr_mult must be positive")
        return self


@dataclass
class _Track(DayTrack):
    atr: AverageTrueRange | None = None
    rsi: RelativeStrengthIndex | None = None
    trend: ExponentialMovingAverage | None = None
    entered_bar: int | None = None
    stop: Decimal | None = None


class RsiPullbackV1(IntradayStrategy):
    name: ClassVar[str] = "rsi_pullback_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = RsiPullbackParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, RsiPullbackParameters):
            raise TypeError("rsi_pullback_v1 needs RsiPullbackParameters")
        self._p: RsiPullbackParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _Track:
        return _Track(
            atr=AverageTrueRange(self._p.atr_period),
            rsi=RelativeStrengthIndex(self._p.rsi_period),
            trend=ExponentialMovingAverage(self._p.trend_ema),
        )

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _Track)
        assert track.atr is not None and track.rsi is not None and track.trend is not None
        if track.start_bar(bar):
            track.entered_bar, track.stop = None, None
        atr = track.atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        rsi = track.rsi.update(bar.close.amount)
        trend = track.trend.update(bar.close.amount)
        if not live or atr is None or rsi is None or trend is None:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held, rsi)
        elif track.bar_of_day > self._p.skip_bars and self._entries_open(bar):
            self._maybe_enter(track, bar, atr, rsi, trend)

    def _manage(self, track: _Track, bar: Candle, held: Position, rsi: Decimal) -> None:
        close = bar.close.amount
        if track.entered_bar is None or track.stop is None:
            return
        if held.is_long:
            reason = (
                "RSI recovered" if rsi >= self._p.rsi_exit
                else "stopped" if close <= track.stop else None
            )  # fmt: skip
        else:
            reason = (
                "RSI recovered" if rsi <= 100 - self._p.rsi_exit
                else "stopped" if close >= track.stop else None
            )  # fmt: skip
        if reason is None and track.bar_of_day - track.entered_bar >= self._p.max_hold_bars:
            reason = "held too long"
        if reason is not None:
            self._exit(bar, held, reason)
            track.entered_bar, track.stop = None, None

    def _maybe_enter(
        self, track: _Track, bar: Candle, atr: Decimal, rsi: Decimal, trend: Decimal
    ) -> None:
        close = bar.close.amount
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if close > trend and rsi <= self._p.rsi_entry:
            side, stop = OrderSide.BUY, close - distance
        elif self._p.allow_short and close < trend and rsi >= 100 - self._p.rsi_entry:
            side, stop = OrderSide.SELL, close + distance
        else:
            return
        if self._enter(
            bar, side, f"RSI{self._p.rsi_period} {rsi:.0f} against the EMA trend", stop=stop
        ):
            track.entered_bar, track.stop = track.bar_of_day, stop
