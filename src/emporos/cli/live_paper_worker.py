"""The trading worker as a process: live market data in, paper orders out.

    Angel One socket ─▶ ticks ─▶ candles ─▶ ClosedBarQueue ─▶ strategies ─▶ RISK ─▶ EXECUTION
                          │                                                            │
                          └─▶ marks / quotes ─────────────────────────────▶ PAPER BROKER (own books)

The market-data side is a `LiveFeed` (see `live_feed`); the venue is the paper broker, so no order
can reach Angel One: the paper broker is handed a source that has no order methods at all.

It runs ONE trading day (start it before the open; it squares off at 15:15 and ends at the close).
Strategies named in `start` begin at once; any other loadable strategy waits for an operator's
START_STRATEGY command (the dashboard), which is why every loadable universe is subscribed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from emporos.cli.history_runtime import instrument_master
from emporos.cli.live_feed import FeedRequest, LiveFeedOpener
from emporos.cli.parity_composition import ParityCloseOutHook, open_parity_runtime
from emporos.cli.strategy_composition import build_registry
from emporos.cli.worker_composition import (
    PaperWorkerComposer,
    WorkerTuning,
    bar_queue_for,
    paper_start_gate,
)
from emporos.control.handlers import StartGate
from emporos.core.clock import IST, Clock, Sleeper
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.session import SessionWindow
from emporos.persistence.collections import Collection
from emporos.persistence.mongo import MongoClientFactory
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.config import RiskLimitsLoader
from emporos.risk.kill_switch import FileSentinelKillSwitch
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.session.worker import SessionReport
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver

_LOG = logging.getLogger(__name__)
DEFAULT_PAPER_CASH = "50000"  # the benchmark capital: what the curated results were judged at


@dataclass(frozen=True)
class PaperWorkerOptions:
    strategy_files: Sequence[Path]
    start: Sequence[str] = ()  # strategies to run from the first bar; the rest wait for START
    account_id: str = "paper"
    starting_cash: Money = field(default_factory=lambda: Money.of(DEFAULT_PAPER_CASH))
    # Strategy name -> the standing the operator acknowledges for it, as on the dashboard.
    acknowledged: Mapping[str, str] = field(default_factory=dict)
    kill_switch_collection: str = Collection.KILL_SWITCH  # tests point this at a scratch one
    verdict_collection: str = Collection.STRATEGY_VERDICTS  # ... and this
    tuning: WorkerTuning = field(default_factory=WorkerTuning)
    # EM-185: after close-out, compare the day with the backtest of the same config and store
    # the report. Off unless asked, so a test rig never opens a second Mongo connection.
    parity_report: bool = False


class LivePaperWorker:
    """Composes and runs one paper trading day over a live feed it is given."""

    def __init__(
        self,
        settings: Settings,
        options: PaperWorkerOptions,
        feeds: LiveFeedOpener,
        clock: Clock,
        sleeper: Sleeper,
    ) -> None:
        self._settings = settings
        self._options = options
        self._feeds = feeds
        self._clock = clock
        self._sleeper = sleeper

    async def run(self) -> SessionReport:
        settings, options, clock = self._settings, self._options, self._clock
        mongo = MongoClientFactory(settings)
        try:
            database = mongo.database()
            master = instrument_master(mongo, database)
            instruments = InstrumentCache()
            await instruments.load_from(master)
            registry = build_registry()
            loader = StrategyConfigLoader(StrategyConfigResolver(registry, instruments))
            available = [loader.load_file(path) for path in options.strategy_files]
            configs = self._chosen(available, options.start)
            gate = paper_start_gate(database, available, options.verdict_collection)
            await self._require_startable(gate, configs)
            bars = bar_queue_for(available)
            window = SessionWindow()
            request = FeedRequest(database, instruments, master, bars, window)
            async with self._feeds.open(request) as feed:
                assembly = await PaperWorkerComposer(
                    client=mongo.client,
                    database=database,
                    market=feed.source,
                    bars=bars,
                    registry=registry,
                    configs=configs,
                    available=available,
                    limits=RiskLimitsLoader().load(),
                    fees=FeeScheduleLibrary.from_directory().for_date(
                        clock.now().astimezone(IST).date()
                    ),
                    account_id=options.account_id,
                    clock=clock,
                    sleeper=self._sleeper,
                    ids=IdGenerator(),
                    kill_switch_sentinel=FileSentinelKillSwitch(settings.kill_switch_path),
                    kill_switch_collection=options.kill_switch_collection,
                    tuning=options.tuning,
                    window=window,
                    starting_cash=options.starting_cash,
                    warmup=feed.warmup,
                    start_gate=gate,
                    close_out_hooks=self._close_out_hooks(settings),
                ).build()
                async with feed.running():
                    _LOG.info("paper worker running: %d strategies loadable", len(available))
                    return await assembly.worker.run_session()
        finally:
            await mongo.close()

    def _close_out_hooks(self, settings: Settings) -> tuple[ParityCloseOutHook, ...]:
        if not self._options.parity_report:
            return ()
        cash = self._options.starting_cash
        return (ParityCloseOutHook(lambda: open_parity_runtime(settings, cash), self._clock),)

    async def _require_startable(
        self, gate: StartGate, configs: Sequence[ResolvedStrategyConfig]
    ) -> None:
        """A strategy named at launch is held to the same rule as a dashboard START."""
        for config in configs:
            refusal = await gate.check(config.name, self._options.acknowledged.get(config.name))
            if refusal is not None:
                raise ConfigurationError(refusal)

    @staticmethod
    def _chosen(
        available: Sequence[ResolvedStrategyConfig], names: Sequence[str]
    ) -> list[ResolvedStrategyConfig]:
        known = {c.name: c for c in available}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ConfigurationError(f"unknown strategy: {', '.join(unknown)}")
        return [known[n] for n in names]
