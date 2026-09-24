"""vwap_reversion_v1 — fade a stretched move back to the session VWAP.

A bar that closes `entry_z` ATRs BELOW the session VWAP with RSI oversold is bought; one `entry_z`
ATRs ABOVE it with RSI overbought is sold short. The trade aims at the VWAP itself: it exits when
price gets back to it, when it moves `stop_atr_mult` ATRs further against the entry, or after
`max_hold_bars` bars. No entries in the first `skip_bars` bars of the day (VWAP means little until
volume has traded) or after `session.no_new_entries_after`; a `cooldown_bars` pause follows every
exit so one stretched stretch is not traded repeatedly.
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
from emporos.strategies.indicators import AverageTrueRange, RelativeStrengthIndex
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy


class VwapReversionParameters(StrategyParameters):
    entry_z: ExactDecimal = Decimal(2)
    atr_period: PositiveInt = 14
    rsi_period: PositiveInt = 7
    rsi_extreme: ExactDecimal = Decimal(30)  # long at <= this, short at >= 100 - this
    stop_atr_mult: ExactDecimal = Decimal("1.5")
    max_hold_bars: PositiveInt = 12
    skip_bars: NonNegativeInt = 6
    cooldown_bars: NonNegativeInt = 6
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> VwapReversionParameters:
        if self.entry_z <= 0 or self.stop_atr_mult <= 0:
            raise ValueError("entry_z and stop_atr_mult must be positive")
        if not 0 < self.rsi_extreme < 50:
            raise ValueError("rsi_extreme must be between 0 and 50")
        return self


@dataclass
class _Track(DayTrack):
    atr: AverageTrueRange | None = None
    rsi: RelativeStrengthIndex | None = None
    entered_bar: int | None = None
    stop: Decimal | None = None
    cooldown_until: int = 0


class VwapReversionV1(IntradayStrategy):
    name: ClassVar[str] = "vwap_reversion_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = VwapReversionParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, VwapReversionParameters):
            raise TypeError("vwap_reversion_v1 needs VwapReversionParameters")
        self._p: VwapReversionParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _Track:
        return _Track(
            atr=AverageTrueRange(self._p.atr_period), rsi=RelativeStrengthIndex(self._p.rsi_period)
        )

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _Track) and track.atr is not None and track.rsi is not None
        if track.start_bar(bar):
            track.entered_bar, track.stop, track.cooldown_until = None, None, 0
        vwap = track.vwap.update(bar)
        atr = track.atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        rsi = track.rsi.update(bar.close.amount)
        if not live or vwap is None or atr is None or atr <= 0 or rsi is None:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held, vwap)
        elif (
            track.bar_of_day > self._p.skip_bars
            and track.bar_of_day > track.cooldown_until
            and self._entries_open(bar)
        ):
            self._maybe_enter(track, bar, vwap, atr, rsi)

    def _manage(self, track: _Track, bar: Candle, held: Position, vwap: Decimal) -> None:
        close = bar.close.amount
        if track.entered_bar is None or track.stop is None:
            return
        if held.is_long:
            reason = "back at VWAP" if close >= vwap else "stopped" if close <= track.stop else None
        else:
            reason = "back at VWAP" if close <= vwap else "stopped" if close >= track.stop else None
        if reason is None and track.bar_of_day - track.entered_bar >= self._p.max_hold_bars:
            reason = "held too long"
        if reason is not None:
            self._exit(bar, held, reason)
            track.entered_bar, track.stop = None, None
            track.cooldown_until = track.bar_of_day + self._p.cooldown_bars

    def _maybe_enter(
        self, track: _Track, bar: Candle, vwap: Decimal, atr: Decimal, rsi: Decimal
    ) -> None:
        close = bar.close.amount
        z = ar.div(ar.sub(close, vwap), atr)
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if z <= -self._p.entry_z and rsi <= self._p.rsi_extreme:
            side, stop = OrderSide.BUY, close - distance
        elif self._p.allow_short and z >= self._p.entry_z and rsi >= 100 - self._p.rsi_extreme:
            side, stop = OrderSide.SELL, close + distance
        else:
            return
        if self._enter(bar, side, f"{z:.2f} ATRs from VWAP {vwap:.2f}, RSI {rsi:.0f}", stop=stop):
            track.entered_bar, track.stop = track.bar_of_day, stop
