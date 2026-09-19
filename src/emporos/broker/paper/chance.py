"""A source of randomness the simulation can be told to pin.

Probabilistic behaviour (random rejects, lost replies) draws from a `Chance`, so a run is
reproducible from a seed and a test can force either outcome without patching anything.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Protocol

ONE = Decimal(1)


class Chance(Protocol):
    def hits(self, probability: Decimal) -> bool:
        """True with the given probability (0 never, 1 always)."""
        ...


class SeededChance:
    """Reproducible pseudo-randomness: the same seed replays the same run."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def hits(self, probability: Decimal) -> bool:
        if not Decimal(0) <= probability <= ONE:
            raise ValueError("a probability lies between 0 and 1")
        # Exact for the boundaries; in between it compares a uniform draw with the probability.
        return Decimal(str(self._random.random())) < probability


class NeverChance:
    """Nothing random ever happens: the deterministic default."""

    def hits(self, probability: Decimal) -> bool:
        return False
