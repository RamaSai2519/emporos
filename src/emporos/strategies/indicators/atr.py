"""Wilder's Average True Range."""

from __future__ import annotations

from decimal import Decimal

from emporos.strategies.indicators import arithmetic as ar


class AverageTrueRange:
    """ATR over `period` bars. True range is the largest of high-low, |high - previous close| and
    |low - previous close| (just high-low for the first bar). The first ATR is the simple mean of
    the first `period` true ranges, then `atr = (atr * (period - 1) + tr) / period`.
    Ready after `period` bars."""

    def __init__(self, period: int) -> None:
        self._period = ar.require_period(period)
        self._previous_close: Decimal | None = None
        self._seen = 0
        self._tr_sum = ar.ZERO
        self._value: Decimal | None = None

    @property
    def period(self) -> int:
        return self._period

    @property
    def value(self) -> Decimal | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal | None:
        if high < low:
            raise ValueError("a bar's high cannot be below its low")
        true_range = self._true_range(high, low)
        self._previous_close = close
        if self._value is None:
            self._seen += 1
            self._tr_sum = ar.add(self._tr_sum, true_range)
            if self._seen == self._period:
                self._value = ar.div(self._tr_sum, Decimal(self._period))
        else:
            keep = ar.mul(self._value, Decimal(self._period - 1))
            self._value = ar.div(ar.add(keep, true_range), Decimal(self._period))
        return self._value

    def _true_range(self, high: Decimal, low: Decimal) -> Decimal:
        span = ar.sub(high, low)
        if self._previous_close is None:
            return span
        return max(
            span,
            ar.sub(high, self._previous_close).copy_abs(),
            ar.sub(low, self._previous_close).copy_abs(),
        )
