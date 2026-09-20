"""Running the curation plan: for each strategy, walk-forward tuning on its pre-declared grid, then
the criteria applied to the out-of-sample result. Composition of parts that already exist; it adds
no way to look at a test window before parameters are chosen."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from emporos.backtest.costs import ScheduleSource
from emporos.backtest.curation import CurationRecord, StrategyCurator
from emporos.backtest.engine import BacktestEngine, BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.backtest.job import ResolverTickSizes
from emporos.backtest.pricing import GateContext, SignalGate
from emporos.backtest.robustness.assessment import AssessorFactory
from emporos.backtest.robustness.recording import NoRecording, ResultRecorder
from emporos.backtest.tuning import BestScoreSelector, ConfigVariants, Objective, ParameterCandidate
from emporos.backtest.universe import AsOfInstruments
from emporos.backtest.walkforward import WalkForwardMode, WalkForwardPlanner
from emporos.backtest.walkforward_run import WalkForwardRunner
from emporos.core.clock import IST
from emporos.domain.instruments import InstrumentResolver
from emporos.domain.money import Money
from emporos.persistence.candles import CandleReader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.registry import StrategyRegistry


@dataclass(frozen=True)
class PlannedStrategy:
    name: str
    config_for: Callable[[InstrumentResolver], ResolvedStrategyConfig]
    candidates: tuple[ParameterCandidate, ...]


@dataclass(frozen=True)
class WindowPlan:
    first_day: date
    last_day: date
    train: timedelta
    test: timedelta
    embargo: timedelta

    def bounds(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self.first_day, time(0, 0), tzinfo=IST).astimezone(UTC)
        end = datetime.combine(self.last_day + timedelta(days=1), time(0, 0), tzinfo=IST)
        return start, end.astimezone(UTC)


class Progress(Protocol):
    def __call__(self, message: str) -> None: ...


class CurationRun:
    def __init__(
        self,
        reader: CandleReader,
        registry: StrategyRegistry,
        instruments: AsOfInstruments,
        schedules: Callable[[], ScheduleSource],
        gate: Callable[[GateContext], SignalGate],
        curator: StrategyCurator,
        objective: Objective,
        assume_current_universe: bool = True,
        recorder: ResultRecorder | None = None,
        assessors: AssessorFactory | None = None,
    ) -> None:
        self._reader = reader
        self._registry = registry
        self._instruments = instruments
        self._schedules = schedules
        self._gate = gate
        self._curator = curator
        self._objective = objective
        self._assume = assume_current_universe
        self._recorder = recorder or NoRecording()
        self._assessors = assessors

    async def run(
        self,
        strategies: Sequence[PlannedStrategy],
        plan: WindowPlan,
        starting_cash: Money,
        progress: Progress = lambda _message: None,
    ) -> list[CurationRecord]:
        start, end = plan.bounds()
        windows = WalkForwardPlanner().plan(
            WalkForwardMode.ROLLING, start, end, plan.train, plan.test, embargo=plan.embargo
        )
        universe = self._instruments.as_of(start, assume_earliest_before_history=self._assume)
        records: list[CurationRecord] = []
        for planned in strategies:
            progress(
                f"{planned.name}: {len(planned.candidates)} candidates x {len(windows)} windows"
            )
            config = planned.config_for(universe.resolver)
            engine = BacktestEngine(
                self._reader, self._registry, ResolverTickSizes(universe.resolver),
                self._schedules, gate=self._gate,
            )  # fmt: skip
            traced = _Traced(engine, progress)
            runner = WalkForwardRunner(
                traced, BestScoreSelector(), self._objective, ConfigVariants()
            )
            base = BacktestSpec(
                config=config, window=FeedWindow(start, end), starting_cash=starting_cash,
                assumptions=("universe resolved from earliest recorded definitions",),
            )  # fmt: skip
            result = await runner.run(base, planned.candidates, windows)
            await self._recorder.record(planned.name, result)
            record = self._curator.evaluate(
                planned.name, [c.name for c in planned.candidates], result
            )
            if self._assessors is not None:
                progress(f"{planned.name}: assessing robustness")
                assessor = self._assessors(traced, universe.resolver)
                report = await assessor.assess(planned.name, result, base, planned.candidates)
                record = replace(record, robustness=report)
            records.append(record)
            progress(f"{planned.name}: {'PASSED' if record.verdict.passed else 'FAILED'}")
        return records


class _Traced:
    """Reports each backtest as it starts, so a long run is visibly alive."""

    def __init__(self, engine: BacktestEngine, progress: Progress) -> None:
        self._engine = engine
        self._progress = progress
        self._n = 0

    async def run(self, spec: BacktestSpec) -> BacktestResult:
        self._n += 1
        self._progress(f"  run {self._n}: {spec.window.start.date()} .. {spec.window.end.date()}")
        return await self._engine.run(spec)
