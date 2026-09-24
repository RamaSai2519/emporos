"""donchian_v1 — buy a break of the session's own channel and ride it with a trailing stop.

The channel is the highest high and lowest low of the previous `lookback` bars OF TODAY, so nothing
trades until `lookback` bars have been seen and the overnight gap never counts as a breakout. A
bar that CLOSES above the channel high (below the low) by `breakout_buffer_bps` is the entry. The
initial stop is `stop_atr_mult` ATRs against the entry; it trails `trail_atr_mult` ATRs behind the
best close since entry, never loosening. Whatever is left is flattened by the session's square-off.
At most `max_entries` per instrument per day. Only continuation is traded here: the failed-breakout
variant is a separate strategy and is not built yet (EM-122).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
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


class DonchianParameters(StrategyParameters):
    lookback: PositiveInt = 20
    atr_period: PositiveInt = 14
    breakout_buffer_bps: ExactDecimal = Decimal(5)
    stop_atr_mult: ExactDecimal = Decimal("1.5")
    trail_atr_mult: ExactDecimal = Decimal(2)
    max_entries: PositiveInt = 2
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> DonchianParameters:
        if self.stop_atr_mult <= 0 or self.trail_atr_mult <= 0 or self.breakout_buffer_bps < 0:
            raise ValueError("stop and trail must be positive, the buffer non-negative")
        return self


@dataclass
class _Track(DayTrack):
    atr: AverageTrueRange | None = None
    highs: deque[Decimal] = field(default_factory=deque)
    lows: deque[Decimal] = field(default_factory=deque)
    entries: int = 0
    stop: Decimal | None = None
    best: Decimal | None = None  # best close since entry, in the trade's favour


class DonchianV1(IntradayStrategy):
    name: ClassVar[str] = "donchian_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = DonchianParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, DonchianParameters):
            raise TypeError("donchian_v1 needs DonchianParameters")
        self._p: DonchianParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _Track:
        return _Track(atr=AverageTrueRange(self._p.atr_period))

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _Track) and track.atr is not None
        if track.start_bar(bar):
            track.highs.clear()
            track.lows.clear()
            track.entries, track.stop, track.best = 0, None, None
        channel_high = max(track.highs) if len(track.highs) == self._p.lookback else None
        channel_low = min(track.lows) if len(track.lows) == self._p.lookback else None
        track.highs.append(bar.high.amount)
        track.lows.append(bar.low.amount)
        if len(track.highs) > self._p.lookback:
            track.highs.popleft()
            track.lows.popleft()
        atr = track.atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        if not live or atr is None or channel_high is None or channel_low is None:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held, atr)
        elif track.entries < self._p.max_entries and self._entries_open(bar):
            self._maybe_enter(track, bar, atr, channel_high, channel_low)

    def _manage(self, track: _Track, bar: Candle, held: Position, atr: Decimal) -> None:
        close = bar.close.amount
        if track.stop is None or track.best is None:
            return
        trail = ar.mul(atr, self._p.trail_atr_mult)
        if held.is_long:
            track.best = max(track.best, close)
            track.stop = max(track.stop, track.best - trail)
            stopped = close <= track.stop
        else:
            track.best = min(track.best, close)
            track.stop = min(track.stop, track.best + trail)
            stopped = close >= track.stop
        if stopped:
            self._exit(bar, held, f"{'long' if held.is_long else 'short'} trailed out")
            track.stop = track.best = None

    def _maybe_enter(
        self, track: _Track, bar: Candle, atr: Decimal, high: Decimal, low: Decimal
    ) -> None:
        close = bar.close.amount
        buffer = self._p.breakout_buffer_bps / _BPS
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if close > high * (1 + buffer):
            side, stop = OrderSide.BUY, close - distance
        elif self._p.allow_short and close < low * (1 - buffer):
            side, stop = OrderSide.SELL, close + distance
        else:
            return
        if self._enter(
            bar, side, f"closed beyond the {self._p.lookback}-bar channel {low}..{high}", stop=stop
        ):
            track.entries += 1
            track.stop, track.best = stop, close
