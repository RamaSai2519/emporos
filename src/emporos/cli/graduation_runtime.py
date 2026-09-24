"""The graduation console over real stores: the one place the evidence ports are bound (EM-189)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.backtest.provenance import quarantine_hash
from emporos.cli.experiment_evidence import FileExperimentEvidence
from emporos.cli.graduation_composition import LIVE_RISK_TIER
from emporos.cli.graduation_console import GraduationConsole, StrategySubjects
from emporos.cli.live_launch import MonitorSwitchView
from emporos.cli.strategy_composition import build_registry
from emporos.cli.verdict_commands import current_instruments
from emporos.core.clock import AsyncioSleeper, Clock
from emporos.core.config import Settings
from emporos.core.ids import IdGenerator
from emporos.graduation.config import GraduationSettingsLoader
from emporos.graduation.policy import standard_policy
from emporos.graduation.requirements import (
    BrokerVerificationPassed,
    DataIntegrityClean,
    HoldoutEvaluated,
    JevDependencyAllowed,
    LiveAcknowledged,
    NoOpenAnomalies,
    PaperReconciliationPassed,
    PromotionRequirement,
    ValidatedVerdictForConfig,
)
from emporos.graduation.service import GraduationService
from emporos.graduation.unverified import UnverifiedBrokerEvidence
from emporos.history.quarantine import CorporateActionQuarantine
from emporos.parity.config import ParityThresholdsLoader
from emporos.persistence.graduation_store import MongoAcknowledgementBook, MongoGraduationLedger
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.parity_store import MongoParityReportStore
from emporos.persistence.quarantine_store import MongoQuarantineStore
from emporos.persistence.repositories import KillSwitchRepository, OrderRepository
from emporos.persistence.verdict_store import MongoVerdictBook
from emporos.risk.config import RiskLimitsLoader
from emporos.risk.kill_switch import FileSentinelKillSwitch, KillSwitchMonitor, MongoKillSwitch
from emporos.session.strategy_files import STRATEGY_CONFIG_DIR, StrategyConfigLoader
from emporos.strategies.resolution import StrategyConfigResolver
from emporos.strategies.snapshot import ConfigSnapshotter

Database = AsyncDatabase[Mapping[str, Any]]


class MongoUnresolvedOrders:
    """Orders the worker has not been able to resolve: UNKNOWN or still PENDING_NEW."""

    def __init__(self, database: Database) -> None:
        self._orders = OrderRepository(database)

    async def count(self) -> int:
        return await self._orders.count({"state": {"$in": ["UNKNOWN", "PENDING_NEW"]}})


class MongoQuarantineHash:
    """The content hash of the corporate-action quarantine in force now, as research stamps it."""

    def __init__(self, database: Database) -> None:
        self._store = MongoQuarantineStore(database)

    async def hash(self) -> str:
        return quarantine_hash(CorporateActionQuarantine(await self._store.load_all()))


class GraduationComposer:
    """Builds the graduation console over real stores. The one place the evidence ports are bound:

    * verdicts, parity reports (EM-185), the acknowledgement book and the ledger: Mongo;
    * experiment reports: the published files in `docs/strategies/experiments`;
    * broker verification (EM-186): `UnverifiedBrokerEvidence`, which reports every critical check
      as NOT passed until EM-186 records real evidence. Nothing here ever assumes a pass.
    """

    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._settings = settings
        self._clock = clock

    def open(self) -> _GraduationSession:
        return _GraduationSession(self._settings, self._clock)


class _GraduationSession:
    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._settings = settings
        self._clock = clock
        self._mongo = MongoClientFactory(settings)

    async def __aenter__(self) -> GraduationConsole:
        return await self._build()

    async def __aexit__(self, *exc: object) -> None:
        await self._mongo.close()

    async def _build(self) -> GraduationConsole:
        settings, clock = self._settings, self._clock
        database = self._mongo.database()
        ids = IdGenerator()
        ledger = MongoGraduationLedger(database, ids)
        acknowledgements = MongoAcknowledgementBook(database, ids)
        verdicts = MongoVerdictBook(database)
        experiments = FileExperimentEvidence()
        tuning = GraduationSettingsLoader().load()
        kill_switch = KillSwitchMonitor(
            [
                FileSentinelKillSwitch(settings.kill_switch_path),
                MongoKillSwitch(KillSwitchRepository(database)),
            ],
            clock,
            AsyncioSleeper(),
        )
        paper: list[PromotionRequirement] = [
            ValidatedVerdictForConfig(verdicts),
            HoldoutEvaluated(experiments),
            DataIntegrityClean(
                experiments,
                MongoQuarantineHash(database),
                frozenset(tuning.allowed_assumed_instruments),
            ),
        ]
        live: list[PromotionRequirement] = [
            PaperReconciliationPassed(
                MongoParityReportStore(database), ParityThresholdsLoader().load().min_sessions
            ),
            BrokerVerificationPassed(
                UnverifiedBrokerEvidence(), clock, tuning.broker_verification_max_age
            ),
            LiveAcknowledged(acknowledgements),
            NoOpenAnomalies(MonitorSwitchView(kill_switch), MongoUnresolvedOrders(database)),
            JevDependencyAllowed(experiments),
        ]
        service = GraduationService(ledger, standard_policy(paper, live), clock)
        return GraduationConsole(
            service,
            await self._subjects(database),
            acknowledgements,
            experiments,
            verdicts,
            clock,
            LIVE_RISK_TIER,
            RiskLimitsLoader.for_tier(LIVE_RISK_TIER).load(),
        )

    @staticmethod
    async def _subjects(database: Database) -> StrategySubjects:
        instruments = await current_instruments(database)
        loader = StrategyConfigLoader(StrategyConfigResolver(build_registry(), instruments))
        snapshotter = ConfigSnapshotter()
        configs = [loader.load_file(path) for path in sorted(STRATEGY_CONFIG_DIR.glob("*.yaml"))]
        return StrategySubjects({c.name: snapshotter.take(c).behaviour_hash for c in configs})
