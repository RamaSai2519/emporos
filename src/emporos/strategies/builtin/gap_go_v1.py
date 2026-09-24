"""gap_go_v1 — a big overnight gap that holds its opening range keeps going.

A session that opens `min_gap_bps` or more (and no more than `max_gap_bps`) away from the previous
close, up or down, is a gap day. Once the opening range (`range_bars` bars) is done, the first bar
that CLOSES beyond the range in the gap's own direction is the entry: long above a gap up, short
below a gap down. The stop is the far side of the opening range, the target `target_r` times that
distance; both are checked on bar closes and anything left is flattened by the session's
square-off. One entry per instrument per day. There is no market-direction (NIFTY) filter: the
platform holds no index bars, so that filter is not tested (EM-124).
"""

from __future__ import annotations

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
from emporos.strategies.gaps import GapTrack
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy


class GapGoParameters(StrategyParameters):
    min_gap_bps: ExactDecimal = Decimal(100)
    max_gap_bps: ExactDecimal = Decimal(500)
    range_bars: PositiveInt = 3
    target_r: ExactDecimal = Decimal("2.0")
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> GapGoParameters:
        if not 0 < self.min_gap_bps < self.max_gap_bps:
            raise ValueError("need 0 < min_gap_bps < max_gap_bps")
        if self.target_r <= 0:
            raise ValueError("target_r must be positive")
        return self


class GapGoV1(IntradayStrategy):
    name: ClassVar[str] = "gap_go_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = GapGoParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, GapGoParameters):
            raise TypeError("gap_go_v1 needs GapGoParameters")
        self._p: GapGoParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> GapTrack:
        return GapTrack()

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, GapTrack)
        track.advance(bar, self._p.range_bars)
        if not live or track.bar_of_day <= self._p.range_bars or not track.range_ready:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held)
        elif not track.traded and self._entries_open(bar):
            self._maybe_enter(track, bar)

    def _manage(self, track: GapTrack, bar: Candle, held: Position) -> None:
        close = bar.close.amount
        if track.stop is None or track.target is None:
            return
        if held.is_long and (close <= track.stop or close >= track.target):
            self._exit(bar, held, f"long {'stopped' if close <= track.stop else 'at target'}")
        elif not held.is_long and (close >= track.stop or close <= track.target):
            self._exit(bar, held, f"short {'stopped' if close >= track.stop else 'at target'}")

    def _maybe_enter(self, track: GapTrack, bar: Candle) -> None:
        gap = track.gap_bps
        if gap is None or not self._p.min_gap_bps <= abs(gap) <= self._p.max_gap_bps:
            return
        assert track.range_high is not None and track.range_low is not None
        close = bar.close.amount
        if gap > 0 and close > track.range_high:
            side, stop = OrderSide.BUY, track.range_low
            target = close + ar.mul(close - stop, self._p.target_r)
        elif gap < 0 and self._p.allow_short and close < track.range_low:
            side, stop = OrderSide.SELL, track.range_high
            target = close - ar.mul(stop - close, self._p.target_r)
        else:
            return
        if stop == close:
            return
        if self._enter(
            bar, side, f"gap {gap:.0f} bps held its opening range and broke out", stop=stop
        ):
            track.traded, track.stop, track.target = True, stop, target
