"""A picklable recipe for the process-pool tests: workers rebuild the same in-memory engine the
serial reference uses, so a parallel batch can be compared with a serial one bit for bit."""

from __future__ import annotations

import os
from dataclasses import dataclass

from emporos.backtest.batch import Backtester
from emporos.backtest.engine import BacktestResult, BacktestSpec
from emporos.domain.money import Money
from tests.support.backtest_engine import WORKED_DAY, bars, engine

SESSIONS = 6
BROKEN_CASH = Money.of("1")  # a spec starting with this cash makes the backtester raise
DEADLY_CASH = Money.of("2")  # ... and this one kills the worker process outright


def session_bars():  # type: ignore[no-untyped-def]
    return [bar for day in range(SESSIONS) for bar in bars(WORKED_DAY, day=day)]


class MisbehavingBacktester:
    """The real engine, except for two marked specs."""

    def __init__(self) -> None:
        self._engine = engine(session_bars())

    async def run(self, spec: BacktestSpec) -> BacktestResult:
        if spec.starting_cash == BROKEN_CASH:
            raise ValueError("the marked run failed")
        if spec.starting_cash == DEADLY_CASH:
            os._exit(1)
        return await self._engine.run(spec)


@dataclass(frozen=True)
class EngineRecipe:
    def build(self) -> Backtester:
        return MisbehavingBacktester()
