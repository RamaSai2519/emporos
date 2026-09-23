"""Composition root for parity (EM-185): read-only Mongo, the candles the live feed persisted, the
fee schedules and the same risk limits paper ran under. No broker, no credentials, no way to place
an order: parity only reads what already happened and replays it in a backtest.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import date

from pymongo.asynchronous.database import AsyncDatabase

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.engine import BacktestEngine
from emporos.backtest.job import ResolverTickSizes
from emporos.backtest.journal import BacktestEventSink
from emporos.backtest.provenance import ProvenanceSnapshotter, ResearchProvenance
from emporos.backtest.risk_gate import RiskGateFactory
from emporos.backtest.universe import AsOfInstruments
from emporos.cli.backtest_runtime import BacktestRuntime, open_backtest_runtime
from emporos.cli.strategy_composition import build_registry
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import IST, Clock, SystemClock
from emporos.core.config import Settings
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.quarantine import CorporateActionQuarantine
from emporos.marketdata.session import SessionWindow
from emporos.parity.config import ParityThresholdsLoader
from emporos.parity.inputs import PaperRunData
from emporos.parity.service import ParityService
from emporos.parity.shadow import ShadowBacktest
from emporos.parity.verdict import ParityPolicy
from emporos.persistence.parity_store import MongoParityReportStore
from emporos.persistence.records import StrategyRunRecord
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    RiskEventRepository,
    SignalRepository,
    StrategyRunRepository,
)
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.config import RiskLimitsLoader
from emporos.risk.limits import RiskLimits
from emporos.session.close_out import CloseOutResult
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.registry import StrategyRegistry

_LOG = logging.getLogger(__name__)
DEFAULT_PARITY_CASH = "50000"  # the paper account's starting cash (live_paper_worker)
CLOSE_OUT_TIMEOUT_SECONDS = 300.0


class MongoPaperRunSource:
    """The paper side of a comparison, read from the platform's own collections."""

    def __init__(self, database: AsyncDatabase) -> None:  # type: ignore[type-arg]
        self._runs = StrategyRunRepository(database)
        self._signals = SignalRepository(database)
        self._orders = OrderRepository(database)
        self._events = OrderEventRepository(database)
        self._executions = ExecutionRepository(database)
        self._risk = RiskEventRepository(database)

    async def runs_on(self, session_date: str) -> tuple[StrategyRunRecord, ...]:
        runs = await self._runs.find({"session_date": session_date})
        return tuple(sorted(runs, key=lambda r: (r.created_at, r.id)))

    async def load(self, run: StrategyRunRecord) -> PaperRunData:
        signals = await self._signals.for_run(run.id)
        signal_ids = [s.id for s in signals]
        orders = await self._orders.find(
            {"$or": [{"signal_id": {"$in": signal_ids}}, {"strategy_run_id": run.id}]}
        )
        order_ids = [o.id for o in orders]
        events = await self._events.find({"order_id": {"$in": order_ids}}) if order_ids else []
        executions = (
            await self._executions.find({"order_id": {"$in": order_ids}}) if order_ids else []
        )
        risk = await self._risk.find({"strategy_run_id": run.id})
        return PaperRunData(
            run, tuple(signals), tuple(orders), tuple(events), tuple(executions), tuple(risk)
        )


class BacktestShadowEngines:
    """One `BacktestEngine` per session day, wired like a backtest of paper's own risk limits."""

    def __init__(
        self,
        runtime: BacktestRuntime,
        registry: StrategyRegistry,
        library: FeeScheduleLibrary,
        limits: RiskLimits,
    ) -> None:
        self._runtime = runtime
        self._registry = registry
        self._library = library
        self._gate = RiskGateFactory(limits)

    def build(self, session_date: date, sink: BacktestEventSink) -> BacktestEngine:
        universe = self._runtime.instruments.as_of(
            SessionWindow().open_at(session_date), assume_earliest_before_history=True
        )
        return BacktestEngine(
            self._runtime.reader, self._registry, ResolverTickSizes(universe.resolver),
            lambda: EarliestBeforeFirst(self._library), gate=self._gate, sink=sink,
        )  # fmt: skip


class UniverseProvenance:
    """`ResearchProvenance` for a shadow run: the universe, calendar and quarantine it ran on."""

    def __init__(
        self,
        instruments: AsOfInstruments,
        quarantine: CorporateActionQuarantine,
        calendar: StoredTradingCalendar,
    ) -> None:
        self._instruments = instruments
        self._quarantine = quarantine
        self._calendar = calendar

    def provenance_for(
        self, config: ResolvedStrategyConfig, session_date: date
    ) -> ResearchProvenance:
        universe = self._instruments.as_of(
            SessionWindow().open_at(session_date), assume_earliest_before_history=True
        )
        return ProvenanceSnapshotter().take(
            universe, config.instrument_ids, config.timeframe, session_date, session_date,
            self._quarantine, self._calendar.content_hash(),
        )  # fmt: skip


@dataclass(frozen=True)
class ParityRuntime:
    service: ParityService
    store: MongoParityReportStore


@asynccontextmanager
async def open_parity_runtime(
    settings: Settings, starting_cash: Money | None = None, clock: Clock | None = None
) -> AsyncIterator[ParityRuntime]:
    cash = starting_cash or Money.of(DEFAULT_PARITY_CASH)
    async with open_backtest_runtime(settings) as runtime:
        registry = build_registry()
        thresholds = ParityThresholdsLoader().load()
        shadow = ShadowBacktest(
            BacktestShadowEngines(
                runtime, registry, FeeScheduleLibrary.from_directory(), RiskLimitsLoader().load()
            ),
            registry,
            cash,
            provenance=UniverseProvenance(
                runtime.instruments, runtime.quarantine, runtime.calendar
            ),
        )
        store = MongoParityReportStore(runtime.database)
        service = ParityService(
            MongoPaperRunSource(runtime.database), shadow, store,
            ParityPolicy.standard(thresholds), SessionWindow(calendar=runtime.calendar),
            clock or SystemClock(), IdGenerator(), LogAlertSink(),
        )  # fmt: skip
        yield ParityRuntime(service, store)


class ParityCloseOutHook:
    """`CloseOutHook`: after a paper session's books are final, report how it compared with the
    backtest of the same config. Time-boxed, and its failures are the worker's to alert on, never
    to fail the session."""

    name = "parity"

    def __init__(
        self,
        opener: Callable[[], AbstractAsyncContextManager[ParityRuntime]],
        clock: Clock,
        timeout_seconds: float = CLOSE_OUT_TIMEOUT_SECONDS,
    ) -> None:
        self._opener = opener
        self._clock = clock
        self._timeout = timeout_seconds

    async def after_close_out(self, result: CloseOutResult) -> None:
        day = self._clock.now().astimezone(IST).date()
        async with asyncio.timeout(self._timeout):
            async with self._opener() as runtime:
                outcome = await runtime.service.daily(day)
        _LOG.info(
            "parity %s: %d report(s), %d already reported, %d skipped",
            day,
            len(outcome.reports),
            outcome.already_reported,
            len(outcome.skipped),
        )
