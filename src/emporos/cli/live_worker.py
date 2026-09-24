"""`emporos worker live`: the live trading worker, behind the full live gate.

Checks every live condition for every named strategy first, exactly as `run-live` does (same gate
classes) — refused, this prints why and touches nothing, never logging in to Angel One. Only when
every condition holds for every named strategy does it open the live venue (`live_venue.py`) and
run one trading day over the real broker, through the identical worker, risk, execution and
reconciliation code the paper worker runs (`worker_composition.LiveWorkerComposer`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from emporos.cli.history_runtime import instrument_master
from emporos.cli.live_feed import FeedRequest
from emporos.cli.live_launch import LiveLaunchCheck, LiveLaunchReport, MonitorSwitchView
from emporos.cli.live_venue import LiveVenueOpener
from emporos.cli.strategy_composition import build_registry
from emporos.cli.worker_composition import LiveWorkerComposer, WorkerTuning, bar_queue_for
from emporos.core.clock import IST, Clock, Sleeper
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.core.ids import IdGenerator
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.session import SessionWindow
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.repositories import KillSwitchRepository
from emporos.persistence.verdict_store import MongoVerdictBook
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.config import RiskLimitsLoader
from emporos.risk.kill_switch import FileSentinelKillSwitch, KillSwitchMonitor, MongoKillSwitch
from emporos.session.launch_gate import ConfigLaunchFacts, live_policy
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.session.tripwire_config import TripwireSettingsLoader
from emporos.session.worker import SessionReport
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver


@dataclass(frozen=True)
class LiveWorkerOptions:
    strategy_files: Sequence[Path]
    start: Sequence[str]  # every one of these must clear the gate, or nothing starts
    account_id: str
    tuning: WorkerTuning = field(default_factory=WorkerTuning)


class LiveWorker:
    """Composes and runs one live trading day, behind the full live gate."""

    def __init__(
        self,
        settings: Settings,
        options: LiveWorkerOptions,
        venues: LiveVenueOpener,
        clock: Clock,
        sleeper: Sleeper,
    ) -> None:
        self._settings = settings
        self._options = options
        self._venues = venues
        self._clock = clock
        self._sleeper = sleeper

    async def run(self) -> LiveLaunchReport | SessionReport:
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

            monitor = KillSwitchMonitor(
                [
                    FileSentinelKillSwitch(settings.kill_switch_path),
                    MongoKillSwitch(KillSwitchRepository(database)),
                ],
                clock,
                self._sleeper,
            )
            policy = live_policy(
                MongoVerdictBook(database), settings.live_trading_enabled,
                MonitorSwitchView(monitor),
            )  # fmt: skip
            report = await LiveLaunchCheck(policy, ConfigLaunchFacts(available)).run(
                [c.name for c in configs]
            )
            if report.refusals:
                return report

            bars = bar_queue_for(configs)
            window = SessionWindow()
            request = FeedRequest(database, instruments, master, bars, window)
            live_instruments = sorted({i for c in available for i in c.instrument_ids})
            async with self._venues.open(request, live_instruments) as connection:
                assembly = await LiveWorkerComposer(
                    client=mongo.client,
                    database=database,
                    broker=connection.broker,
                    venue=connection.venue,
                    health=connection.health,
                    live_trading_enabled=settings.live_trading_enabled,
                    bars=bars,
                    registry=registry,
                    configs=configs,
                    limits=RiskLimitsLoader().load(),
                    fees=FeeScheduleLibrary.from_directory().for_date(
                        clock.now().astimezone(IST).date()
                    ),
                    account_id=options.account_id,
                    clock=clock,
                    sleeper=self._sleeper,
                    ids=IdGenerator(),
                    kill_switch_sentinel=FileSentinelKillSwitch(settings.kill_switch_path),
                    window=window,
                    warmup=connection.warmup,
                    tuning=options.tuning,
                    tripwire=TripwireSettingsLoader(settings.yaml_config).load(),
                    feed_watch=connection.watch,
                ).build()
                return await assembly.worker.run_session()
        finally:
            await mongo.close()

    @staticmethod
    def _chosen(
        available: Sequence[ResolvedStrategyConfig], names: Sequence[str]
    ) -> list[ResolvedStrategyConfig]:
        known = {c.name: c for c in available}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ConfigurationError(f"unknown strategy: {', '.join(unknown)}")
        return [known[n] for n in names]
