"""The real `momentum_v1` through the real engine on a deterministic wave, for end-to-end tests.

`document_json()` is synchronous so a fresh interpreter can print it and a test can compare the
text across processes."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from emporos.backtest.document import BacktestDocument
from emporos.backtest.engine import BacktestEngine, BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.cli.strategy_composition import build_registry
from emporos.domain.money import Money
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import FixedSchedule, FixedTicks
from tests.support.strategies import (
    INSTRUMENT,
    OTHER_INSTRUMENT,
    T0,
    momentum_raw,
    resolved,
    session_bars,
    wave_closes,
)

DAYS = 6
PER_DAY = 75


async def momentum_backtest(days: int = DAYS) -> BacktestResult:
    """Two instruments on offset waves, `days` sessions of 75 five-minute bars each."""
    candles = []
    for shift, instrument_id in enumerate((INSTRUMENT, OTHER_INSTRUMENT)):
        closes = wave_closes(days * PER_DAY + 30 * shift, period=90, amplitude=60)[30 * shift :]
        candles += session_bars(closes, instrument_id, per_day=PER_DAY)
    spec = BacktestSpec(
        config=resolved(momentum_raw()),
        window=FeedWindow(T0 - timedelta(hours=1), T0 + timedelta(days=days) + timedelta(hours=10)),
        starting_cash=Money.of("1000000"),
        warmup_bars=0,
    )
    engine = BacktestEngine(
        InMemoryCandles(candles), build_registry(), FixedTicks(), lambda: FixedSchedule()
    )
    return await engine.run(spec)


def document_json() -> str:
    result = asyncio.run(momentum_backtest())
    return json.dumps(BacktestDocument().render(result), sort_keys=True)
