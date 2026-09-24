"""Running the curation plan: for each strategy, walk-forward tuning on its pre-declared grid, then
the criteria applied to the out-of-sample result. Composition of parts that already exist; it adds
no way to look at a test window before parameters are chosen."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from emporos.backtest.batch import BatchBacktester, RunProgress
from emporos.backtest.cost_breakdown import CostBreakdownCalculator
from emporos.backtest.costs import ScheduleSource
from emporos.backtest.curation import CurationRecord, StrategyCurator
from emporos.backtest.engine import BacktestEngine, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.backtest.integrity import ResearchIntegrityGate
from emporos.backtest.job import ResolverTickSizes
from emporos.backtest.pricing import GateContext, SignalGate
from emporos.backtest.provenance import ProvenanceSnapshotter
from emporos.backtest.robustness.assessment import AssessorFactory
from emporos.backtest.robustness.holdout import FinalHoldoutReservation
from emporos.backtest.robustness.recording import NoRecording, ResultRecorder
from emporos.backtest.tuning import BestScoreSelector, ConfigVariants, Objective, ParameterCandidate
from emporos.backtest.universe import AsOfInstruments
from emporos.backtest.walkforward import WalkForwardMode, WalkForwardPlanner
from emporos.backtest.walkforward_run import WalkForwardResult, WalkForwardRunner
from emporos.core.clock import IST
from emporos.domain.instruments import InstrumentResolver
from emporos.domain.money import Money
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.quarantine import CorporateActionQuarantine
from emporos.persistence.candles import CandleReader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.snapshot import ConfigSnapshotter


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
        batch: BatchBacktester | None = None,
        calendar: StoredTradingCalendar | None = None,
        quarantine: CorporateActionQuarantine | None = None,
        allow_quarantined_instruments: bool = False,
        holdout: FinalHoldoutReservation | None = None,
        costs: CostBreakdownCalculator | None = None,
    ) -> None:
        """`batch` runs the independent backtests of a strategy (default: one after another).

        `holdout` (EM-184/EM-188) carves the last days off the plan before anything is planned or
        loaded: the walk-forward only ever sees the rest, and the carved-off days are recorded in
        the robustness provenance. None reserves nothing, which a report calls out as such."""
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
        self._batch = batch
        self._calendar = calendar or StoredTradingCalendar()
        self._quarantine = quarantine or CorporateActionQuarantine()
        self._allow_quarantined = allow_quarantined_instruments
        self._holdout = holdout
        self._costs = costs

    async def run(
        self,
        strategies: Sequence[PlannedStrategy],
        plan: WindowPlan,
        starting_cash: Money,
        progress: Progress = lambda _message: None,
    ) -> list[CurationRecord]:
        plan_start, plan_end = plan.bounds()
        walkable, holdout = self._split(plan_start, plan_end)
        start, end = walkable.start, walkable.end
        last_day = _last_day(end) if holdout is not None else plan.last_day
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
            ResearchIntegrityGate(self._quarantine).check(
                config.instrument_ids, plan.first_day, last_day,
                allow_quarantined=self._allow_quarantined,
            )  # fmt: skip
            provenance = ProvenanceSnapshotter().take(
                universe, config.instrument_ids, config.timeframe, plan.first_day,
                last_day, self._quarantine, self._calendar.content_hash(),
            )  # fmt: skip
            engine = BacktestEngine(
                self._reader, self._registry, ResolverTickSizes(universe.resolver),
                self._schedules, gate=self._gate,
            )  # fmt: skip
            runner = WalkForwardRunner(
                engine, BestScoreSelector(), self._objective, ConfigVariants(), batch=self._batch
            )
            run_log = _RunLog(progress)
            base = BacktestSpec(
                config=config, window=FeedWindow(start, end), starting_cash=starting_cash,
                assumptions=("universe resolved from earliest recorded definitions",),
                provenance=provenance,
            )  # fmt: skip
            result = await runner.run(base, planned.candidates, windows, run_log)
            await self._recorder.record(planned.name, result)
            record = self._curator.evaluate(
                planned.name, [c.name for c in planned.candidates], result
            )
            record = replace(
                record,
                provenance=provenance,
                behaviour_hash=ConfigSnapshotter().take(config).behaviour_hash,
                behaviour_hashes=self._behaviour_hashes(config, result),
                costs=None if self._costs is None else self._costs.of(record.pooled.trades),
            )
            if self._assessors is not None:
                progress(f"{planned.name}: assessing robustness")
                assessor = self._assessors(engine, universe.resolver)
                report = await assessor.assess(
                    planned.name, result, base, planned.candidates, run_log,
                    holdout=holdout,
                )  # fmt: skip
                record = replace(record, robustness=report)
            records.append(record)
            progress(f"{planned.name}: {'PASSED' if record.verdict.passed else 'FAILED'}")
        return records

    def _split(self, start: datetime, end: datetime) -> tuple[FeedWindow, FeedWindow | None]:
        """(what may be walked, what is reserved): nothing downstream is ever handed the reserved
        days, so no choice can have looked at them."""
        if self._holdout is None:
            return FeedWindow(start, end), None
        walkable, reserved = self._holdout.split(start, end)
        return walkable, reserved

    @staticmethod
    def _behaviour_hashes(
        config: ResolvedStrategyConfig, result: WalkForwardResult
    ) -> dict[str, str]:
        """The behaviour hash of each candidate a window actually chose, applied to the base
        config: what a later verdict for that exact configuration is bound to."""
        variants, snapshotter = ConfigVariants(), ConfigSnapshotter()
        return {
            name: snapshotter.take(variants.apply(config, chosen)).behaviour_hash
            for name, chosen in sorted({o.chosen.name: o.chosen for o in result.outcomes}.items())
        }


def _last_day(walkable_end: datetime) -> date:
    """The last calendar day (IST) the walk-forward may read: the day before the end bound."""
    return (walkable_end.astimezone(IST) - timedelta(microseconds=1)).date()


class _RunLog:
    """Reports each backtest as it completes, so a long run is visibly alive."""

    def __init__(self, progress: Progress) -> None:
        self._progress = progress

    def __call__(self, run: RunProgress) -> None:
        outcome = run.outcome
        state = "FAILED" if outcome.failed else "done"
        self._progress(f"  [{run.done}/{run.total}] {outcome.label}: {state}")
