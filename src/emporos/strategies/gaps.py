"""Shared day state for the two gap strategies: the overnight gap and the opening range.

The gap is the first bar's open against the previous session's last close, in basis points (positive
= up). Both come from the bars the strategy has already been shown, warm-up included, so a
strategy never reads a clock or a store for yesterday. With no previous close (the first day ever
seen) there is no gap and nothing trades. The opening range is the high and low of the session's
first `range_bars` bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack

_BPS = Decimal(10_000)


@dataclass
class GapTrack(DayTrack):
    last_close: Decimal | None = None
    prev_close: Decimal | None = None
    gap_bps: Decimal | None = None
    range_high: Decimal | None = None
    range_low: Decimal | None = None
    traded: bool = False
    stop: Decimal | None = None
    target: Decimal | None = None

    def advance(self, bar: Candle, range_bars: int) -> None:
        """Take one closed bar into the day state (new-day reset, gap, opening range)."""
        if self.start_bar(bar):
            self.prev_close = self.last_close
            self.gap_bps = None
            if self.prev_close is not None:
                move = ar.sub(bar.open.amount, self.prev_close)
                self.gap_bps = ar.div(move, self.prev_close) * _BPS
            self.range_high = self.range_low = None
            self.traded, self.stop, self.target = False, None, None
        if self.bar_of_day <= range_bars:
            high, low = bar.high.amount, bar.low.amount
            self.range_high = high if self.range_high is None else max(self.range_high, high)
            self.range_low = low if self.range_low is None else min(self.range_low, low)
        self.last_close = bar.close.amount

    @property
    def range_ready(self) -> bool:
        return self.range_high is not None and self.range_low is not None
