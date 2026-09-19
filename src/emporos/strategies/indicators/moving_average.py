"""Simple and exponential moving averages of a closed-bar series."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from emporos.strategies.indicators import arithmetic as ar


class SimpleMovingAverage:
    """Mean of the last `period` values. Ready after `period` values."""

    def __init__(self, period: int) -> None:
        self._period = ar.require_period(period)
        self._window: deque[Decimal] = deque()
        self._sum = ar.ZERO

    @property
    def period(self) -> int:
        return self._period

    @property
    def value(self) -> Decimal | None:
        if len(self._window) < self._period:
            return None
        return ar.div(self._sum, Decimal(self._period))

    @property
    def ready(self) -> bool:
        return self.value is not None

    def update(self, price: Decimal) -> Decimal | None:
        self._window.append(price)
        self._sum = ar.add(self._sum, price)
        if len(self._window) > self._period:
            self._sum = ar.sub(self._sum, self._window.popleft())
        return self.value


class ExponentialMovingAverage:
    """EMA with smoothing 2 / (period + 1), seeded with the simple average of the first `period`
    values (the convention of TA-Lib and most charting packages). Ready after `period` values."""

    def __init__(self, period: int) -> None:
        self._period = ar.require_period(period)
        self._alpha = ar.div(Decimal(2), Decimal(period + 1))
        self._seed = SimpleMovingAverage(period)
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

    def update(self, price: Decimal) -> Decimal | None:
        if self._value is None:
            self._value = self._seed.update(price)
        else:
            self._value = ar.add(self._value, ar.mul(self._alpha, ar.sub(price, self._value)))
        return self._value
