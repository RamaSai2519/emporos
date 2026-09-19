"""Wilder's Relative Strength Index."""

from __future__ import annotations

from decimal import Decimal

from emporos.strategies.indicators import arithmetic as ar

NEUTRAL = Decimal(50)


class RelativeStrengthIndex:
    """RSI over `period` changes, with Wilder smoothing: the first average gain and loss are simple
    means of the first `period` changes, then `avg = (avg * (period - 1) + change) / period`.
    Ready after `period + 1` values (the first value only sets the baseline).

    RSI = 100 - 100 / (1 + avg_gain / avg_loss). With no losses it is 100; with neither gains nor
    losses (a flat market) it is 50 — undefined mathematically, neutral by convention here.
    """

    def __init__(self, period: int) -> None:
        self._period = ar.require_period(period)
        self._previous: Decimal | None = None
        self._changes = 0
        self._gain_sum = ar.ZERO
        self._loss_sum = ar.ZERO
        self._avg_gain: Decimal | None = None
        self._avg_loss: Decimal | None = None

    @property
    def period(self) -> int:
        return self._period

    @property
    def value(self) -> Decimal | None:
        if self._avg_gain is None or self._avg_loss is None:
            return None
        if self._avg_loss == 0:
            return NEUTRAL if self._avg_gain == 0 else ar.HUNDRED
        strength = ar.div(self._avg_gain, self._avg_loss)
        return ar.sub(ar.HUNDRED, ar.div(ar.HUNDRED, ar.add(ar.ONE, strength)))

    @property
    def ready(self) -> bool:
        return self._avg_gain is not None

    def update(self, price: Decimal) -> Decimal | None:
        if self._previous is not None:
            self._absorb(ar.sub(price, self._previous))
        self._previous = price
        return self.value

    def _absorb(self, change: Decimal) -> None:
        gain = max(change, ar.ZERO)
        loss = max(ar.sub(ar.ZERO, change), ar.ZERO)
        if self._avg_gain is None or self._avg_loss is None:
            self._changes += 1
            self._gain_sum = ar.add(self._gain_sum, gain)
            self._loss_sum = ar.add(self._loss_sum, loss)
            if self._changes == self._period:
                self._avg_gain = ar.div(self._gain_sum, Decimal(self._period))
                self._avg_loss = ar.div(self._loss_sum, Decimal(self._period))
            return
        keep = Decimal(self._period - 1)
        period = Decimal(self._period)
        self._avg_gain = ar.div(ar.add(ar.mul(self._avg_gain, keep), gain), period)
        self._avg_loss = ar.div(ar.add(ar.mul(self._avg_loss, keep), loss), period)
