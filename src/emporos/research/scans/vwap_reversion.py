"""The `vwap_reversion_v1` rules as a scan (EM-191 F3b): fade a stretched move back to the VWAP.

Mirrors `strategies.builtin.vwap_reversion_v1` where it decides.
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
from emporos.strategies.builtin.vwap_reversion_v1 import VwapReversionParameters
from emporos.strategies.indicators import AverageTrueRange, RelativeStrengthIndex
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack

__all__ = ["VwapReversionRules", "vwap_reversion_scan"]


class VwapReversionRules:
    def __init__(self, parameters: VwapReversionParameters, no_new_entries_after: time) -> None:
        self._p = parameters
        self._cutoff = no_new_entries_after
        self._track = DayTrack()
        self._atr = AverageTrueRange(parameters.atr_period)
        self._rsi = RelativeStrengthIndex(parameters.rsi_period)
        self._entered_bar: int | None = None
        self._stop: Decimal | None = None
        self._cooldown_until = 0

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        track = self._track
        if track.start_bar(bar):
            self._entered_bar, self._stop, self._cooldown_until = None, None, 0
        vwap = track.vwap.update(bar)
        atr = self._atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        rsi = self._rsi.update(bar.close.amount)
        if not live or vwap is None or atr is None or atr <= 0 or rsi is None:
            return None
        if held is not None:
            return self._manage(bar, held, vwap)
        if (
            track.bar_of_day > self._p.skip_bars
            and track.bar_of_day > self._cooldown_until
            and entries_open(bar, self._cutoff)
        ):
            return self._maybe_enter(bar, vwap, atr, rsi, can_afford)
        return None

    def _manage(self, bar: Candle, held: OrderSide, vwap: Decimal) -> ScanIntent:
        close = bar.close.amount
        if self._entered_bar is None or self._stop is None:
            return None
        if held is OrderSide.BUY:
            reason = "vwap" if close >= vwap else "stopped" if close <= self._stop else None
        else:
            reason = "vwap" if close <= vwap else "stopped" if close >= self._stop else None
        if reason is None and self._track.bar_of_day - self._entered_bar >= self._p.max_hold_bars:
            reason = "held too long"
        if reason is None:
            return None
        self._entered_bar, self._stop = None, None
        self._cooldown_until = self._track.bar_of_day + self._p.cooldown_bars
        return ExitIntent()

    def _maybe_enter(
        self, bar: Candle, vwap: Decimal, atr: Decimal, rsi: Decimal, can_afford: bool
    ) -> ScanIntent:
        close = bar.close.amount
        z = ar.div(ar.sub(close, vwap), atr)
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if z <= -self._p.entry_z and rsi <= self._p.rsi_extreme:
            side, stop = OrderSide.BUY, close - distance
        elif self._p.allow_short and z >= self._p.entry_z and rsi >= 100 - self._p.rsi_extreme:
            side, stop = OrderSide.SELL, close + distance
        else:
            return None
        if not can_afford:
            return None
        self._entered_bar, self._stop = self._track.bar_of_day, stop
        return EntryIntent(side)


def vwap_reversion_scan(
    parameters: VwapReversionParameters, execution: ScanExecution
) -> IntradayScan:
    return IntradayScan(
        "vwap_reversion_v1",
        lambda: VwapReversionRules(parameters, execution.no_new_entries_after),
        execution,
    )
