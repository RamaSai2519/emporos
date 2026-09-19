"""`StrategyContext` — the whole world a strategy can see (plan.md §9).

It exposes bar history, the strategy's own positions, a clock, a logger, its configuration and a
seeded random source. It exposes NO broker, no order method, no storage handle, and no way to
obtain one: the fields below are the complete list, each typed as a narrow read-only Protocol or a
plain value. A strategy that wants to trade returns a `Signal`; risk and execution decide the rest.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from emporos.core.clock import Clock
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.history import BarHistory
from emporos.strategies.positions import PositionView


@dataclass(frozen=True, slots=True)
class StrategyContext:
    run_id: str
    config: ResolvedStrategyConfig
    clock: Clock  # the ONLY source of "now": strategy code never reads the wall clock
    logger: logging.Logger
    history: BarHistory  # closed bars only, bounded by `clock`
    positions: PositionView  # this strategy's positions, not the account's
    # Seeded from the run's configuration, so a reproduced run draws the same numbers.
    rng: random.Random

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("a strategy context needs a run id")
