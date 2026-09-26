"""The random-entry control (EM-219 S1): the same book, exits, sizing, costs and risk engine, the
same instruments on the same days, but the entry MOMENT and DIRECTION are chance.

For every entry the arm actually took, one control entry on that instrument and day, at a uniformly
random 5-minute bar between 09:20 and 14:45 and a fair-coin direction. What the control removes is
exactly what the method claims to add: the crossing (timing) and the side it points to. 200 seeded
runs per arm; the run's seed is derived from the master seed, the arm and the run number, so any run
can be replayed alone."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date

from emporos.research.s1.engine import Trade
from emporos.research.s1.signals import Signal

__all__ = ["CONTROL_SEED", "FIRST_MINUTE", "LAST_MINUTE", "RandomEntries"]

CONTROL_SEED = 20260926
FIRST_MINUTE = 9 * 60 + 20  # the first bar's end that can be a decision
LAST_MINUTE = 14 * 60 + 45


class RandomEntries:
    def __init__(self, seed: int = CONTROL_SEED) -> None:
        self._seed = seed

    def signals(self, arm_name: str, run: int, taken: Sequence[Trade]) -> dict[date, list[Signal]]:
        rng = random.Random(f"{self._seed}:{arm_name}:{run}")
        slots = range(FIRST_MINUTE, LAST_MINUTE + 1, 5)
        out: dict[date, list[Signal]] = {}
        for trade in taken:
            signal = Signal(
                trade.instrument_id, trade.name, trade.day, rng.choice(slots),
                rng.choice((1, -1)),
            )  # fmt: skip
            out.setdefault(trade.day, []).append(signal)
        for signals in out.values():
            signals.sort(key=lambda s: (s.minute, s.name, s.direction))
        return out
