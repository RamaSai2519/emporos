"""A long hot tier (the old defaults, before Mongo was cut to what the live pipeline needs).

Tests of WHERE a bar goes use ages that only make sense with these boundaries; the shipped defaults
are pinned by their own test in `tests/unit/persistence/test_placement.py`.
"""

from __future__ import annotations

from emporos.domain.candles import Timeframe

LONG_HOT: dict[Timeframe, int | None] = {
    Timeframe.M1: 90,
    Timeframe.M5: 365,
    Timeframe.M15: 365,
    Timeframe.H1: 365,
    Timeframe.D1: None,
}
