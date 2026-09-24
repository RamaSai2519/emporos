"""Composition root for `emporos backtest jev-compare` (EM-187): the pieces that need files,
Mongo or the network, wired into the domain-level collaborators the experiment is built from.

Nothing here decides anything about Jev: the guard, the sweep, the analysis and the policy live in
`emporos.jev` and `emporos.backtest`. This module reads the plan, builds the engines and the
provider stack, and reads the recorded verdicts the baseline is folded from.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from emporos.backtest.costs import ScheduleSource
from emporos.backtest.feed import FeedWindow
from emporos.backtest.jev_pnl import BacktestRunner
from emporos.backtest.multi_engine import MultiStrategyBacktestEngine
from emporos.backtest.pricing import GateContext, SignalGate, TickSizes
from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.domain.money import Money
from emporos.domain.research_experiments import DatePair
from emporos.domain.verdicts import RecordedVerdict, Standing, standing_of
from emporos.jev.leakage import SymbolAnonymiser
from emporos.jev.prompts import JevPrompt
from emporos.jev.protocol import JevProvider
from emporos.jev.replay import (
    JevDecisionJournal,
    RecordMissesJevProvider,
    ReplayJevProvider,
)
from emporos.opportunity.allocator import AllocationConstraints
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from emporos.persistence.candles import CandleReader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.snapshot import ConfigSnapshotter

DEFAULT_PLAN = Path("config/jev/jev_incremental_v1.plan.yaml")

_PLAN_KEYS = frozenset({"strategies", "constraints", "holdout_days"})
_CONSTRAINT_KEYS = frozenset(
    {"max_simultaneous_positions", "max_risk_per_trade", "max_portfolio_risk"}
)


@dataclass(frozen=True)
class JevPlan:
    """Which strategies form the portfolio, its allocation limits, and the reserved holdout."""

    strategy_files: tuple[Path, ...]
    max_simultaneous_positions: int
    max_risk_per_trade: Money
    max_portfolio_risk: Money
    holdout_days: int | None

    def constraints(self, capital: Money) -> AllocationConstraints:
        return AllocationConstraints(
            production_capital=capital,
            max_simultaneous_positions=self.max_simultaneous_positions,
            max_risk_per_trade=self.max_risk_per_trade,
            max_portfolio_risk=self.max_portfolio_risk,
        )


class JevPlanLoader:
    """Read as strictly as a declaration: unknown keys are refused and money is a quoted string."""

    def load(self, path: Path) -> JevPlan:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ConfigurationError(f"cannot read the Jev plan {path}: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{path} must be a mapping")
        self._keys(path, "plan", document, _PLAN_KEYS)
        constraints = document.get("constraints")
        if not isinstance(constraints, dict):
            raise ConfigurationError(f"{path}: constraints must be a mapping")
        self._keys(path, "constraints", constraints, _CONSTRAINT_KEYS)
        files = document.get("strategies")
        if not isinstance(files, list) or not files:
            raise ConfigurationError(f"{path}: strategies must be a non-empty list of files")
        holdout = document.get("holdout_days")
        if holdout is not None and (
            isinstance(holdout, bool) or not isinstance(holdout, int) or holdout <= 0
        ):
            raise ConfigurationError(f"{path}: holdout_days must be a positive whole number")
        try:
            return JevPlan(
                strategy_files=tuple(Path(str(f)) for f in files),
                max_simultaneous_positions=int(constraints["max_simultaneous_positions"]),
                max_risk_per_trade=self._money(path, constraints, "max_risk_per_trade"),
                max_portfolio_risk=self._money(path, constraints, "max_portfolio_risk"),
                holdout_days=holdout,
            )
        except KeyError as error:
            raise ConfigurationError(f"{path}: constraints is missing {error}") from error

    @staticmethod
    def _keys(path: Path, where: str, document: Mapping[str, Any], allowed: frozenset[str]) -> None:
        unknown = sorted(set(document) - allowed)
        if unknown:
            raise ConfigurationError(f"{path}: unknown {where} key(s) {', '.join(unknown)}")

    @staticmethod
    def _money(path: Path, document: Mapping[str, Any], key: str) -> Money:
        value = document[key]
        if isinstance(value, bool | float) or not isinstance(value, int | str):
            raise ConfigurationError(f"{path}: {key} must be an integer or a quoted string")
        try:
            return Money(Decimal(str(value)))
        except InvalidOperation:
            raise ConfigurationError(f"{path}: {key} is not a number") from None


class TradingDayWindow:
    """From midnight IST of the first day to midnight IST after the last, as UTC."""

    @staticmethod
    def of(first: date, last: date) -> FeedWindow:
        start = datetime.combine(first, time(0, 0), tzinfo=IST)
        end = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=IST)
        return FeedWindow(start.astimezone(UTC), end.astimezone(UTC))


@dataclass(frozen=True)
class SplitDays:
    first: date
    last: date  # the last day the experiment may read
    holdout: DatePair | None  # reserved after `last`, never read by the experiment


class HoldoutSplit:
    """Reserves the last `holdout_days` of the requested range before anything runs: the
    experiment reads only what is left, and the report names the reserved days."""

    def split(self, first: date, last: date, holdout_days: int | None) -> SplitDays:
        if first > last:
            raise ValueError("the first day cannot be after the last")
        if holdout_days is None:
            return SplitDays(first, last, None)
        reserved_first = last - timedelta(days=holdout_days - 1)
        if reserved_first <= first:
            raise ValueError(
                f"a {holdout_days}-day holdout leaves no days to run in {first}..{last}"
            )
        return SplitDays(first, reserved_first - timedelta(days=1), DatePair(reserved_first, last))


class MultiStrategyEngineFactory:
    """Builds one arm. Every collaborator is fixed at construction, and `build` takes only the
    Jev filter, so two arms from the same factory can differ in nothing else."""

    def __init__(
        self,
        reader: CandleReader,
        registry: StrategyRegistry,
        ticks: TickSizes,
        schedules: Callable[[], ScheduleSource],
        gate: Callable[[GateContext], SignalGate],
    ) -> None:
        self._reader = reader
        self._registry = registry
        self._ticks = ticks
        self._schedules = schedules
        self._gate = gate

    def build(self, jev_filter: JevMetaDecisionFilter | None) -> BacktestRunner:
        return MultiStrategyBacktestEngine(
            self._reader,
            self._registry,
            self._ticks,
            self._schedules,
            gate=self._gate,
            jev_filter=jev_filter,
        )


class JevProviderStack:
    """The provider an experiment asks, layered outside-in: anonymise, then the journal, then (in
    record mode only) the live model. The journal keys on what the model would have seen, so the
    anonymised request is what is recorded and replayed."""

    def __init__(
        self,
        journal: JevDecisionJournal,
        prompt: JevPrompt,
        model: str,
        salt: str,
        anonymise: bool,
    ) -> None:
        self._journal = journal
        self._prompt = prompt
        self._model = model
        self._salt = salt
        self._anonymise = anonymise

    def replay(self) -> JevProvider:
        return self._outer(ReplayJevProvider(self._journal, self._prompt, self._model))

    def record(self, live: JevProvider, mode: str) -> JevProvider:
        recorder = RecordMissesJevProvider(self._journal, live, self._prompt, self._model, mode)
        return self._outer(recorder)

    def _outer(self, inner: JevProvider) -> JevProvider:
        if not self._anonymise:
            return inner
        return SymbolAnonymiser(inner, self._salt)


class BaselineStandings:
    """Where each strategy of the plan stands today: its recorded verdict against the config file
    as it is now (the same behaviour hash `emporos backtest verdicts` binds a verdict to)."""

    def __init__(self, latest: Callable[[str], RecordedVerdict | None]) -> None:
        self._latest = latest

    def of(self, configs: Mapping[str, ResolvedStrategyConfig]) -> dict[str, Standing]:
        standings: dict[str, Standing] = {}
        for name, config in configs.items():
            behaviour_hash = ConfigSnapshotter().take(config).behaviour_hash
            standings[name] = standing_of(self._latest(name), behaviour_hash)
        return standings
