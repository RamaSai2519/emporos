"""orb_v1 — opening-range breakout.

The first `range_bars` bars of the session set an opening range. Once it is complete, the first bar
that CLOSES beyond it (by `breakout_buffer_bps`) is a breakout: go with it. One entry per
instrument per day. The stop is `stop_atr_mult` ATRs against the entry and the target is
`target_r` times that distance; both are checked on bar closes, and whatever is still open is
flattened by the session's square-off. A range narrower than `min_range_bps` or wider than
`max_range_bps` of the price is skipped (too thin to mean anything, or already a full day's move).
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
    PositiveInt,
    ResolvedStrategyConfig,
    StrategyParameters,
)
from emporos.strategies.indicators import AverageTrueRange
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy

_BPS = Decimal(10_000)


class OrbParameters(StrategyParameters):
    range_bars: PositiveInt = 6
    breakout_buffer_bps: ExactDecimal = Decimal(5)
    atr_period: PositiveInt = 14
    stop_atr_mult: ExactDecimal = Decimal(1)
    target_r: ExactDecimal = Decimal(2)
    min_range_bps: ExactDecimal = Decimal(20)
    max_range_bps: ExactDecimal = Decimal(150)
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> OrbParameters:
        if self.stop_atr_mult <= 0 or self.target_r <= 0 or self.breakout_buffer_bps < 0:
            raise ValueError("stop, target and buffer must be positive (buffer non-negative)")
        if not 0 <= self.min_range_bps < self.max_range_bps:
            raise ValueError("need 0 <= min_range_bps < max_range_bps")
        return self


@dataclass
class _OrbTrack(DayTrack):
    atr: AverageTrueRange | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    traded: bool = False
    stop: Decimal | None = None
    target: Decimal | None = None


class OrbV1(IntradayStrategy):
    name: ClassVar[str] = "orb_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = OrbParameters

    def __init__(self, config: "ResolvedStrategyConfig") -> None:  # noqa: UP037
        if not isinstance(config.parameters, OrbParameters):
            raise TypeError("orb_v1 needs OrbParameters")
        self._p: OrbParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _OrbTrack:
        return _OrbTrack(atr=AverageTrueRange(self._p.atr_period))

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _OrbTrack) and track.atr is not None
        if track.start_bar(bar):
            track.high = track.low = None
            track.traded, track.stop, track.target = False, None, None
        atr = track.atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        if track.bar_of_day <= self._p.range_bars:
            high, low = bar.high.amount, bar.low.amount
            track.high = high if track.high is None else max(track.high, high)
            track.low = low if track.low is None else min(track.low, low)
            return
        if not live or atr is None or track.high is None or track.low is None:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held)
        elif not track.traded and self._entries_open(bar):
            self._maybe_enter(track, bar, atr)

    def _manage(self, track: _OrbTrack, bar: Candle, held: "Position") -> None:  # noqa: UP037
        close = bar.close.amount
        if track.stop is None or track.target is None:
            return
        if held.is_long and (close <= track.stop or close >= track.target):
            self._exit(bar, held, f"long {'stopped' if close <= track.stop else 'at target'}")
        elif not held.is_long and (close >= track.stop or close <= track.target):
            self._exit(bar, held, f"short {'stopped' if close >= track.stop else 'at target'}")

    def _maybe_enter(self, track: _OrbTrack, bar: Candle, atr: Decimal) -> None:
        assert track.high is not None and track.low is not None
        close = bar.close.amount
        width_bps = ar.div(ar.sub(track.high, track.low), close) * _BPS
        if not self._p.min_range_bps <= width_bps <= self._p.max_range_bps:
            return
        buffer = self._p.breakout_buffer_bps / _BPS
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if close > track.high * (1 + buffer):
            side, stop, target = (
                OrderSide.BUY,
                close - distance,
                close + distance * self._p.target_r,
            )
        elif self._p.allow_short and close < track.low * (1 - buffer):
            side, stop, target = (
                OrderSide.SELL,
                close + distance,
                close - distance * self._p.target_r,
            )
        else:
            return
        why = f"closed {close} beyond the opening range {track.low}..{track.high}"
        if self._enter(bar, side, why):
            track.traded, track.stop, track.target = True, stop, target
