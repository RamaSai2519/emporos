"""Technical indicators: pure, deterministic, incremental (plan.md §9, EM-68).

Each indicator is fed one closed bar's value at a time and reports `None` until it has seen enough
history to be meaningful — the warm-up prefix that must never produce a signal (plan.md §10). All
arithmetic is `Decimal` under one fixed context, so a value never depends on ambient interpreter
settings and never touches a float.
"""

from emporos.strategies.indicators.atr import AverageTrueRange
from emporos.strategies.indicators.moving_average import (
    ExponentialMovingAverage,
    SimpleMovingAverage,
)
from emporos.strategies.indicators.rsi import RelativeStrengthIndex

__all__ = [
    "AverageTrueRange",
    "ExponentialMovingAverage",
    "RelativeStrengthIndex",
    "SimpleMovingAverage",
]
