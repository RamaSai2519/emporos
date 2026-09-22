"""Kaufman's Efficiency Ratio: net directional movement over total movement covered."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from emporos.strategies.indicators import arithmetic as ar


class EfficiencyRatio:
    """ER over `period` bars: |close[t] - close[t-period]| / sum(|close[i] - close[i-1]|), in
    [0, 1]. Near 1 means price covered ground in one direction (trending); near 0 means it moved a
    lot while going nowhere net (choppy/ranging). Ready after `period + 1` closes."""

    def __init__(self, period: int) -> None:
        self._period = ar.require_period(period)
        self._closes: deque[Decimal] = deque(maxlen=period + 1)
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

    def update(self, close: Decimal) -> Decimal | None:
        self._closes.append(close)
        if len(self._closes) < self._period + 1:
            return None
        net_change = ar.sub(self._closes[-1], self._closes[0]).copy_abs()
        total_movement = ar.ZERO
        previous = self._closes[0]
        for price in list(self._closes)[1:]:
            total_movement = ar.add(total_movement, ar.sub(price, previous).copy_abs())
            previous = price
        self._value = ar.ZERO if total_movement == ar.ZERO else ar.div(net_change, total_movement)
        return self._value
