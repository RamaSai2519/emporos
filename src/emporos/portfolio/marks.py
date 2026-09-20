"""Latest traded price per instrument, from the tick stream — what unrealised P&L is marked at."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from emporos.core.clock import Clock
from emporos.domain.money import Money
from emporos.domain.ticks import Tick


@dataclass(frozen=True)
class Mark:
    price: Money
    at: datetime


class LatestTickMarks:
    """Keeps the most recent in-order tick per instrument; a stale mark is no mark.

    An out-of-order tick is older news than the one already held, so it never replaces it. A mark
    older than `max_age` is withheld: valuing a position at a price from minutes ago would report
    a P&L that is not real, and `PortfolioValuator` reports a missing mark honestly instead.
    """

    def __init__(self, clock: Clock, max_age: timedelta = timedelta(seconds=120)) -> None:
        if max_age <= timedelta(0):
            raise ValueError("a mark must be allowed to live for a positive time")
        self._clock = clock
        self._max_age = max_age
        self._latest: dict[str, Mark] = {}

    def on_tick(self, tick: Tick) -> None:
        held = self._latest.get(tick.instrument_id)
        if tick.out_of_order or (held is not None and tick.exchange_ts < held.at):
            return
        self._latest[tick.instrument_id] = Mark(tick.ltp, tick.exchange_ts)

    def marks(self) -> Mapping[str, Money]:
        now = self._clock.now()
        return {i: m.price for i, m in self._latest.items() if now - m.at <= self._max_age}
