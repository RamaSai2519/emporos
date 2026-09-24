"""The `orb_v1` rules as a scan (EM-191 F3b): opening-range breakout, one entry per day.

Mirrors `strategies.builtin.orb_v1` where it decides; the parity test holds it to the real
strategy through the real engine.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import (
    EntryIntent,
    ExitIntent,
    IntradayScan,
    ScanExecution,
    ScanIntent,
    entries_open,
)
from emporos.strategies.builtin.orb_v1 import OrbParameters
from emporos.strategies.indicators import AverageTrueRange
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack

__all__ = ["OrbRules", "orb_scan"]

_BPS = Decimal(10_000)


class OrbRules:
    def __init__(self, parameters: OrbParameters, no_new_entries_after: time) -> None:
        self._p = parameters
        self._cutoff = no_new_entries_after
        self._track = DayTrack()
        self._atr = AverageTrueRange(parameters.atr_period)
        self._high: Decimal | None = None
        self._low: Decimal | None = None
        self._traded = False
        self._stop: Decimal | None = None
        self._target: Decimal | None = None

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        if self._track.start_bar(bar):
            self._high = self._low = None
            self._traded, self._stop, self._target = False, None, None
        atr = self._atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        if self._track.bar_of_day <= self._p.range_bars:
            high, low = bar.high.amount, bar.low.amount
            self._high = high if self._high is None else max(self._high, high)
            self._low = low if self._low is None else min(self._low, low)
            return None
        if not live or atr is None or self._high is None or self._low is None:
            return None
        if held is not None:
            return self._manage(bar, held)
        if not self._traded and entries_open(bar, self._cutoff):
            return self._maybe_enter(bar, atr, can_afford)
        return None

    def _manage(self, bar: Candle, held: OrderSide) -> ScanIntent:
        close = bar.close.amount
        if self._stop is None or self._target is None:
            return None
        if held is OrderSide.BUY and (close <= self._stop or close >= self._target):
            return ExitIntent()
        if held is OrderSide.SELL and (close >= self._stop or close <= self._target):
            return ExitIntent()
        return None

    def _maybe_enter(self, bar: Candle, atr: Decimal, can_afford: bool) -> ScanIntent:
        assert self._high is not None and self._low is not None
        close = bar.close.amount
        width_bps = ar.div(ar.sub(self._high, self._low), close) * _BPS
        if not self._p.min_range_bps <= width_bps <= self._p.max_range_bps:
            return None
        buffer = self._p.breakout_buffer_bps / _BPS
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if close > self._high * (1 + buffer):
            side, stop, target = (
                OrderSide.BUY,
                close - distance,
                close + distance * self._p.target_r,
            )
        elif self._p.allow_short and close < self._low * (1 - buffer):
            side, stop, target = (
                OrderSide.SELL,
                close + distance,
                close - distance * self._p.target_r,
            )
        else:
            return None
        if not can_afford:
            return None
        self._traded, self._stop, self._target = True, stop, target
        return EntryIntent(side)


def orb_scan(parameters: OrbParameters, execution: ScanExecution) -> IntradayScan:
    return IntradayScan(
        "orb_v1", lambda: OrbRules(parameters, execution.no_new_entries_after), execution
    )
