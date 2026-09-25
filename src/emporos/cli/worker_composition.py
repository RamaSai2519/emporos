"""Composition root for the trading worker in PAPER mode: the only place its parts are chosen.

    market data ─▶ marks ─────────────────────────────▶ risk facts ─┐
    strategies ─▶ signals ─▶ recorder ─▶ RISK ─▶ EXECUTION ─▶ paper broker (its own books)
                                                       │              │ trade book / order updates
                                platform books ◀── fill processor ◀───┘
                                     │
                       reconciler (halts through the kill switch) · snapshots · square-off

Everything a strategy signal, a square-off and (later) a dashboard command does to a broker goes
through the one `GatedExecutionSink`. The worker holds no credentials: the venue behind it is the
paper broker, whose only outside contact is a market-data source. Live trading would swap the
venue and broker here and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol

from pymongo import AsyncMongoClient

from emporos.backtest.engine import DEFAULT_WARMUP_BARS, DEFAULT_WARMUP_LOOKBACK
from emporos.backtest.feed import WarmupLoader
from emporos.broker.angelone.endpoints import EndpointGroup
from emporos.broker.angelone.limits import ANGELONE_RATE_LIMITS
from emporos.broker.base import Broker
from emporos.broker.models import MarketDataMode
from emporos.broker.paper.broker import PaperBroker
from emporos.broker.paper.costs import ScheduledCosts
from emporos.broker.paper.factory import PaperBrokerConfig, PaperBrokerFactory
from emporos.broker.paper.market import MarketDataSource
from emporos.broker.ratelimit import GroupRateLimiter
from emporos.cli.graduation_composition import live_graduation, stage_view
from emporos.cli.paper_composition import PaperComposer
from emporos.cli.quote_recording import QuoteRecordingPlan
from emporos.control.commands import CommandType
from emporos.control.handlers import (
    BackfillHandler,
    BacktestHandler,
    CancelOrderHandler,
    ClosePositionHandler,
    KillSwitchHandler,
    PlaceManualOrderHandler,
    ReconcileNowHandler,
    SquareOffAllHandler,
    StartGate,
    StartStrategyHandler,
    StopStrategyHandler,
    TradingModeHandler,
    UpdateStrategyConfigHandler,
)
from emporos.control.jobs import BackgroundJobs, JobFunction
from emporos.control.processor import CommandHandler, CommandProcessor
from emporos.control.strategies import ConfigValidator, StrategyController
from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock, Sleeper
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.fees import FeeSchedule, IntradayCharges
from emporos.domain.marketable import MarketableLimit
from emporos.domain.money import Money
from emporos.domain.trading_mode import TradingMode
from emporos.execution.engine import ExecutionEngine
from emporos.execution.fills import FillProcessor, FillSynchroniser
from emporos.execution.gateway import BrokerOrderGateway
from emporos.execution.order_updates import OrderUpdateTranslator
from emporos.execution.pricing import MarketableLimitPricer
from emporos.execution.reprice_scheduler import RepriceScheduler
from emporos.execution.repricing import RepriceCoordinator, RepricePolicy
from emporos.execution.state import OrderStateMachine
from emporos.execution.tracking import InFlightOrders, TrackedOrderGateway
from emporos.history.calendar import CalendarStore, StoredTradingCalendar
from emporos.marketdata.session import SessionWindow
from emporos.observability.alerts import (
    EventOutbox,
    LifecycleEvents,
    OutboxAlertSink,
    WorkerHealthReports,
)
from emporos.observability.metrics import EmfMetricsSink, MetricsPublisher
from emporos.persistence.candles import CandleReader
from emporos.persistence.collections import Collection
from emporos.persistence.instrument_ticks import InstrumentTickSizes
from emporos.persistence.ledger_reader import PlatformLedger
from emporos.persistence.order_journal import MongoOrderJournal
from emporos.persistence.paper_books import PaperBooks
from emporos.persistence.paper_journal import MongoPaperJournal
from emporos.persistence.repositories import (
    CommandRepository,
    CommandResultRepository,
    Database,
    ExecutionRepository,
    InstrumentRepository,
    KillSwitchRepository,
    OrderEventRepository,
    OrderRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
    ReconciliationRunRepository,
    RiskEventRepository,
    SignalRepository,
    StrategyRepository,
    StrategyRunRepository,
    SystemEventRepository,
)
from emporos.persistence.transactions import TransactionRunner
from emporos.persistence.verdict_store import MongoVerdictBook
from emporos.portfolio.ledger import PortfolioValuator, PositionCalculator
from emporos.portfolio.marks import LatestTickMarks
from emporos.portfolio.reconciliation import (
    Reconciler,
    ReconciliationComparator,
    ReconciliationTracker,
    ReconciliationTrigger,
)
from emporos.portfolio.replay import PositionReplay
from emporos.portfolio.service import PortfolioService
from emporos.portfolio.snapshots import SnapshotSchedule, SnapshotService
from emporos.portfolio.sources import BrokerReconciliationSource
from emporos.risk.assembly import MonitoredSystemFacts, SnapshotAssembler
from emporos.risk.config import RiskTier
from emporos.risk.engine import RiskEngine
from emporos.risk.kill_switch import (
    TRIPWIRE_SETTER,
    FileSentinelKillSwitch,
    KillSwitchControl,
    KillSwitchMonitor,
    KillSwitchReader,
    KillSwitchWriter,
    MongoKillSwitch,
)
from emporos.risk.limits import RiskLimits
from emporos.risk.rejection_log import MongoRejectionLog
from emporos.risk.standard import StandardRuleSet
from emporos.session.bar_feed import ClosedBarQueue
from emporos.session.close_out import CloseOutHook, EndOfDay
from emporos.session.decision_quotes import QuoteCapturingMarketFacts
from emporos.session.halt import KillSwitchHalt
from emporos.session.host import ManagedRun, StrategyHost
from emporos.session.jobs import Job, JobScheduler
from emporos.session.launch_gate import (
    ConfigLaunchFacts,
    PolicyStartGate,
    SwitchView,
    live_policy,
    paper_policy,
)
from emporos.session.lifecycle import SessionLifecycle
from emporos.session.quoter import MarkRepriceQuoter
from emporos.session.recovery import StartupRecovery
from emporos.session.rejection_tracker import RejectionTracker
from emporos.session.replacement import RiskReplacementReviewer
from emporos.session.risk_facts import (
    JournalOrderFlow,
    LedgerAccountFacts,
    QuotedMarketFacts,
    VenueHealth,
)
from emporos.session.run_status import RunStatusBoard
from emporos.session.signal_path import GatedExecutionSink
from emporos.session.square_off import SquareOffService, WorkingOrders
from emporos.session.strategy_positions import StrategyPositionBook
from emporos.session.strategy_runs import (
    RunEnvironment,
    StrategyRunLauncher,
    StrategyRunnerBuilder,
)
from emporos.session.telemetry import RejectionCounter, TickRate, WorkerTelemetry
from emporos.session.tripwire import (
    AnomalyDetector,
    AnomalyTripwire,
    FeedDroppedDetector,
    FeedWatch,
    OrderUpdateGapDetector,
    RejectionBurstDetector,
    UnresolvedUnknownOrderDetector,
    WidespreadStalenessDetector,
)
from emporos.session.tripwire_config import TripwireSettings
from emporos.session.updates import OrderUpdateRouter
from emporos.session.worker import SessionSchedule, SessionVenue, TradingWorker
from emporos.signals.recorder import SignalRecorder
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.runner import WallClockSync

# The execution engine's own order-per-second budget (plan.md §12 step 3): the same numbers the
# broker enforces on placeOrder, held independently so a paper session is paced like a live one.
ORDER_BUDGET = "orders"


@dataclass(frozen=True)
class WorkerTuning:
    """Intervals and bounds that are configuration, not code."""

    fill_sync: timedelta = timedelta(seconds=2)
    resolution: timedelta = timedelta(seconds=5)
    reconcile: timedelta = timedelta(minutes=5)
    reprice: timedelta = timedelta(seconds=2)
    commands: timedelta = timedelta(seconds=1)
    metrics: timedelta = timedelta(seconds=60)
    events: timedelta = timedelta(seconds=2)
    kill_switch: timedelta = timedelta(seconds=5)
    reprice_step_bps: Decimal = Decimal(5)
    mark_max_age: timedelta = timedelta(seconds=120)
    snapshot: SnapshotSchedule = field(default_factory=SnapshotSchedule)
    schedule: SessionSchedule = field(default_factory=SessionSchedule)
    # EM-217: record L1 quotes for the D1 names as one more read-only job. None leaves it off.
    quote_recording: QuoteRecordingPlan | None = None


@dataclass(frozen=True)
class WorkerAssembly:
    """The finished worker and the handles a test or an operator command needs to look inside."""

    worker: TradingWorker
    broker: Broker
    engine: ExecutionEngine
    sync: FillSynchroniser
    reconciler: Reconciler
    tracker: ReconciliationTracker
    risk: RiskEngine
    sink: GatedExecutionSink
    marks: LatestTickMarks
    book: StrategyPositionBook
    outbox: EventOutbox
    lifecycle: SessionLifecycle
    ledger: PlatformLedger
    journal: MongoOrderJournal
    monitor: KillSwitchMonitor
    control: KillSwitchControl
    runs: tuple[ManagedRun, ...]
    host: StrategyHost
    processor: CommandProcessor
    telemetry: WorkerTelemetry


class PaperVenue:
    """The 'outside world' of a paper session: no login, and one market-data subscription."""

    def __init__(
        self, broker: PaperBroker, instruments: Sequence[str], health: VenueHealth
    ) -> None:
        self._broker = broker
        self._instruments = list(instruments)
        self._health = health

    async def authenticate(self) -> None:
        await self._broker.ensure_session()

    async def connect(self) -> None:
        await self._broker.subscribe_market_data(self._instruments, MarketDataMode.QUOTE)
        self._health.set(session=True, feed=True)

    async def close(self) -> None:
        self._health.set(session=False, feed=False)


class CashSource(Protocol):
    async def available_cash(self) -> Money | None: ...


class FlushExtra(Protocol):
    """Whatever else must be durable before the worker may end, beyond the outbox's own events."""

    async def flush(self) -> object: ...


class _BrokerCash:
    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    async def available_cash(self) -> Money | None:
        return (await self._broker.get_funds()).available_cash


class _NoExtraFlush:
    async def flush(self) -> object:
        return None


class _JournalFlush:
    def __init__(self, journal: MongoPaperJournal) -> None:
        self._journal = journal

    async def flush(self) -> object:
        await self._journal.flush()
        return None


@dataclass(frozen=True, kw_only=True)
class BrokerSeam:
    """The parts of a worker that depend on which broker is behind it: everything else in
    `_assemble` is the same worker, risk, execution and reconciliation code, run once over
    whichever of these it is given (plan.md: "live trading would swap the venue and broker here
    and nothing else")."""

    broker: Broker
    venue: SessionVenue
    health: VenueHealth
    trading_mode: TradingMode
    live_trading_enabled: bool
    cash: CashSource
    start_gate: StartGate
    extra_jobs: Sequence[Job] = ()
    extra_flush: FlushExtra = field(default_factory=_NoExtraFlush)


class _CommonFields(Protocol):
    """Everything `_assemble` needs that does NOT depend on which broker is behind the worker.
    `PaperWorkerComposer` and `LiveWorkerComposer` both satisfy this structurally."""

    client: AsyncMongoClient[Mapping[str, Any]]
    database: Database
    bars: ClosedBarQueue
    registry: StrategyRegistry
    configs: Sequence[ResolvedStrategyConfig]
    limits: RiskLimits
    fees: FeeSchedule
    account_id: str
    clock: Clock
    sleeper: Sleeper
    ids: IdGenerator
    kill_switch_sentinel: FileSentinelKillSwitch
    window: SessionWindow
    tuning: WorkerTuning
    session_date: date | None
    kill_switch_collection: str
    commands_collection: str
    available: Sequence[ResolvedStrategyConfig]
    config_validator: ConfigValidator | None
    job_runners: Mapping[str, JobFunction]
    warmup: WarmupSource | None
    close_out_hooks: Sequence[CloseOutHook]
    tripwire: TripwireSettings | None
    feed_watch: FeedWatch | None


@dataclass(frozen=True)
class _Prelude:
    """Built once, ahead of the broker-specific seam, because a live `start_gate` needs the kill
    switch view before the seam exists."""

    outbox: EventOutbox
    alerts: AlertSink
    lifecycle: SessionLifecycle
    monitor: KillSwitchMonitor
    control: KillSwitchControl


def _build_prelude(common: _CommonFields) -> _Prelude:
    outbox = EventOutbox()
    alerts: AlertSink = OutboxAlertSink(outbox, common.clock, common.ids)
    lifecycle = SessionLifecycle(
        common.clock, (LifecycleEvents(outbox, common.ids, common.account_id),)
    )
    readers: list[KillSwitchReader] = [common.kill_switch_sentinel]
    writers: list[KillSwitchWriter] = [common.kill_switch_sentinel]
    flag = MongoKillSwitch(KillSwitchRepository(common.database, common.kill_switch_collection))
    readers.append(flag)
    writers.append(flag)
    monitor = KillSwitchMonitor(
        readers, common.clock, common.sleeper, common.tuning.kill_switch.total_seconds()
    )
    control = KillSwitchControl(writers, readers, common.clock)
    return _Prelude(outbox, alerts, lifecycle, monitor, control)


@dataclass(kw_only=True)
class PaperWorkerComposer:
    client: AsyncMongoClient[Mapping[str, Any]]
    database: Database
    market: MarketDataSource
    bars: ClosedBarQueue
    registry: StrategyRegistry
    configs: Sequence[ResolvedStrategyConfig]
    limits: RiskLimits
    fees: FeeSchedule
    account_id: str
    clock: Clock
    sleeper: Sleeper
    ids: IdGenerator
    kill_switch_sentinel: FileSentinelKillSwitch
    window: SessionWindow
    starting_cash: Money = field(default_factory=lambda: Money.of("50000"))
    tuning: WorkerTuning = field(default_factory=WorkerTuning)
    session_date: date | None = None
    kill_switch_collection: str = Collection.KILL_SWITCH
    paper_flush: timedelta = timedelta(seconds=1)
    commands_collection: str = Collection.COMMANDS
    available: Sequence[ResolvedStrategyConfig] = ()  # every loadable strategy, for START
    config_validator: ConfigValidator | None = None
    job_runners: Mapping[str, JobFunction] = field(default_factory=dict)
    warmup: WarmupSource | None = None  # history for a launched strategy; None starts it empty
    close_out_hooks: Sequence[CloseOutHook] = ()
    # EM-189: the anomaly tripwire. None leaves it off (a test rig); the CLI always passes both.
    tripwire: TripwireSettings | None = None
    feed_watch: FeedWatch | None = None
    # May a strategy start? None means the paper rule: a strategy that is not validated needs its
    # standing acknowledged. A test that wants no rule passes `OpenStartGate` explicitly.
    start_gate: StartGate | None = None

    async def build(self) -> WorkerAssembly:
        await self.client.admin.command("ping")  # open the pool before anything fans out (H1)
        prelude = _build_prelude(self)

        # --- the broker: paper, with its own books, fed by a market-data source -----------
        costs = ScheduledCosts(IntradayCharges(self.fees))
        paper_books = PaperBooks.in_database(self.database)
        paper_journal = paper_books.journal(self.client)
        runtime = await PaperComposer(
            source=self.market,
            factory=PaperBrokerFactory(
                PaperBrokerConfig(self.account_id, self.starting_cash), costs
            ),
            journal=paper_journal,
            store=paper_books.store(),
            client_code=self.account_id,
            clock=self.clock,
            ids=self.ids,
            sleeper=self.sleeper,
            alerts=prelude.alerts,
        ).open()
        broker = runtime.broker
        health = VenueHealth()
        instruments = sorted(
            {i for c in (*self.configs, *self.available) for i in c.instrument_ids}
        )
        loadable = {c.name: c for c in (*self.available, *self.configs)}
        seam = BrokerSeam(
            broker=broker,
            venue=PaperVenue(broker, instruments, health),
            health=health,
            trading_mode=TradingMode.PAPER,
            live_trading_enabled=False,  # paper is never live
            cash=_BrokerCash(broker),
            start_gate=self.start_gate or paper_start_gate(self.database, list(loadable.values())),
            extra_jobs=(Job("paper_flush", self.paper_flush, paper_journal.flush),),
            extra_flush=_JournalFlush(paper_journal),
        )
        return await _assemble(self, seam, prelude)


def paper_start_gate(
    database: Database,
    configs: Sequence[ResolvedStrategyConfig],
    verdicts: str = Collection.STRATEGY_VERDICTS,
) -> StartGate:
    """The rule for starting a strategy in paper: any may start, but one that is not validated for
    its current config needs its standing named."""
    book = MongoVerdictBook(database, verdicts)
    return PolicyStartGate(paper_policy(book, stage_view(database)), ConfigLaunchFacts(configs))


def live_start_gate(
    database: Database,
    configs: Sequence[ResolvedStrategyConfig],
    live_trading_enabled: bool,
    kill_switch: SwitchView,
    risk_tier: RiskTier,
    verdicts: str = Collection.STRATEGY_VERDICTS,
) -> StartGate:
    """The rule for starting a strategy in live from the dashboard or API: every live condition
    applies, exactly as it does to the worker's own launch check. There is no acknowledgement path
    here."""
    book = MongoVerdictBook(database, verdicts)
    return PolicyStartGate(
        live_policy(book, live_trading_enabled, kill_switch, live_graduation(database, risk_tier)),
        ConfigLaunchFacts(configs),
    )


@dataclass(kw_only=True)
class LiveWorkerComposer:
    """Composition root for the trading worker in LIVE mode: the same worker, risk, execution and
    reconciliation as paper, over a broker and venue that are already logged in and connected
    (built by `AngelOneLiveVenueOpener`, `cli/live_venue.py`). The only place in this process that
    may hold an order-capable broker."""

    client: AsyncMongoClient[Mapping[str, Any]]
    database: Database
    broker: Broker
    venue: SessionVenue
    health: VenueHealth
    live_trading_enabled: bool
    risk_tier: RiskTier  # which tier `limits` was loaded from; the launch gate checks it
    bars: ClosedBarQueue
    registry: StrategyRegistry
    configs: Sequence[ResolvedStrategyConfig]
    limits: RiskLimits
    fees: FeeSchedule
    account_id: str
    clock: Clock
    sleeper: Sleeper
    ids: IdGenerator
    kill_switch_sentinel: FileSentinelKillSwitch
    window: SessionWindow
    tuning: WorkerTuning = field(default_factory=WorkerTuning)
    session_date: date | None = None
    kill_switch_collection: str = Collection.KILL_SWITCH
    commands_collection: str = Collection.COMMANDS
    available: Sequence[ResolvedStrategyConfig] = ()
    config_validator: ConfigValidator | None = None
    job_runners: Mapping[str, JobFunction] = field(default_factory=dict)
    warmup: WarmupSource | None = None
    start_gate: StartGate | None = None
    close_out_hooks: Sequence[CloseOutHook] = ()
    tripwire: TripwireSettings | None = None
    feed_watch: FeedWatch | None = None

    async def build(self) -> WorkerAssembly:
        await self.client.admin.command("ping")
        prelude = _build_prelude(self)
        loadable = {c.name: c for c in (*self.available, *self.configs)}
        seam = BrokerSeam(
            broker=self.broker,
            venue=self.venue,
            health=self.health,
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=self.live_trading_enabled,
            cash=_BrokerCash(self.broker),
            start_gate=self.start_gate
            or live_start_gate(
                self.database,
                list(loadable.values()),
                self.live_trading_enabled,
                _SwitchView(prelude.monitor),
                self.risk_tier,
            ),  # fmt: skip
        )
        return await _assemble(self, seam, prelude)


async def _assemble(common: _CommonFields, seam: BrokerSeam, prelude: _Prelude) -> WorkerAssembly:
    """The worker, risk, execution and reconciliation machinery that is identical whether `seam`
    came from a paper broker or a live one."""
    db = common.database
    broker = seam.broker
    outbox, alerts, lifecycle = prelude.outbox, prelude.alerts, prelude.lifecycle
    costs = ScheduledCosts(IntradayCharges(common.fees))

    # --- the platform's books: one journal, one ledger --------------------------------
    orders, events = OrderRepository(db), OrderEventRepository(db)
    executions, positions = ExecutionRepository(db), PositionRepository(db)
    journal = MongoOrderJournal(
        orders, events, executions, positions, TransactionRunner(common.client), common.account_id
    )
    ledger = PlatformLedger(common.account_id, orders, executions, positions, common.clock)
    marks = LatestTickMarks(common.clock, common.tuning.mark_max_age)
    broker.on_tick(marks.on_tick)
    tick_rate = TickRate(common.clock)
    broker.on_tick(tick_rate.on_tick)
    ticks = InstrumentTickSizes(InstrumentRepository(db))
    book = StrategyPositionBook(common.account_id, PositionCalculator())
    machine = OrderStateMachine()
    sync = FillSynchroniser(
        broker,
        FillProcessor(
            journal, costs, PositionCalculator(), machine, common.clock, common.ids,
            common.account_id, listeners=[book],
        ),
    )  # fmt: skip
    book.load(
        await ledger.all_executions(),
        {o.id: o for o in await orders.find({"account_id": common.account_id})},
    )
    portfolio = PortfolioService(ledger, marks, PortfolioValuator())

    # --- execution ----------------------------------------------------------------------
    run_buffers: dict[str, MarketableLimit] = {}  # filled as runs start, below
    default_buffer = MarketableLimit(
        max((c.execution.limit_buffer_bps for c in common.configs), default=Decimal(5))
    )
    in_flight = InFlightOrders()
    engine = ExecutionEngine(
        TrackedOrderGateway(BrokerOrderGateway(broker), in_flight), journal,
        GroupRateLimiter(
            {ORDER_BUDGET: ANGELONE_RATE_LIMITS[EndpointGroup.PLACE_ORDER.value]},
            common.clock,
            common.sleeper,
        ),
        common.clock, common.ids, machine,
        MarketableLimitPricer(default_buffer, ticks, run_buffers),
        common.account_id,
    )  # fmt: skip

    # --- reconciliation and risk --------------------------------------------------------
    tracker = ReconciliationTracker(common.clock)
    reconciler = Reconciler(
        BrokerReconciliationSource(ledger, broker),
        ReconciliationComparator(PositionReplay(PositionCalculator())),
        sync,
        _RunLog(ReconciliationRunRepository(db)),
        KillSwitchHalt(prelude.control),
        tracker, common.clock, common.ids, alerts,
    )  # fmt: skip
    decision_quotes = QuoteCapturingMarketFacts(
        QuotedMarketFacts(broker, marks), common.clock, "broker_quote"
    )
    assembler = SnapshotAssembler(
        common.clock,
        MonitoredSystemFacts(
            seam.trading_mode,
            seam.live_trading_enabled,
            prelude.monitor,
            seam.health,
            tracker,
        ),
        LedgerAccountFacts(portfolio, book, marks),
        decision_quotes,
        JournalOrderFlow(journal, ledger, common.clock),
    )
    rejections = RejectionCounter(MongoRejectionLog(RiskEventRepository(db), common.ids))
    risk = RiskEngine(
        StandardRuleSet(common.limits, common.window).rules(),
        assembler,
        rejections,
        common.ids, common.clock, alerts,
    )  # fmt: skip
    recorder = SignalRecorder(SignalRepository(db), common.ids)
    sink = GatedExecutionSink(recorder, risk, engine, alerts, decision_quotes, recorder)

    # --- strategies ---------------------------------------------------------------------
    run_records = StrategyRunRepository(db)
    launcher = StrategyRunLauncher(
        common.registry, StrategyRepository(db), run_records, common.clock, common.ids,
        common.account_id,
    )  # fmt: skip
    runs_board = RunStatusBoard(run_records, common.clock)
    await runs_board.close_open_runs(common.account_id)  # what THIS account's dead worker left
    loadable = {c.name: c for c in (*common.available, *common.configs)}
    for config in loadable.values():
        await launcher.register(config)  # listed before its first run, so it can be started
    builder = StrategyRunnerBuilder(common.registry)
    session_date = (common.session_date or common.clock.now().date()).isoformat()
    factory = _RunFactory(
        launcher, builder, {c.name: c for c in (*common.available, *common.configs)},
        session_date, common.clock, sink, book, alerts, run_buffers, common.warmup,
    )  # fmt: skip
    runs = [await factory.launch(config.name) for config in common.configs]

    # --- jobs ----------------------------------------------------------------------------
    policy = _reprice_policy(common.configs)
    repricer = RepriceScheduler(
        journal,
        RepriceCoordinator(
            engine, RiskReplacementReviewer(risk, common.clock), policy, common.clock, sync
        ),
        policy,
        MarkRepriceQuoter(marks, ticks, common.tuning.reprice_step_bps),
        common.clock, alerts,
    )  # fmt: skip
    snapshots = SnapshotService(
        common.account_id, portfolio, seam.cash, journal,
        PortfolioSnapshotRepository(db), common.clock,
    )  # fmt: skip
    updates = OrderUpdateRouter()
    broker.on_order_update(updates.on_update)
    t = common.tuning
    health_reports = WorkerHealthReports(
        outbox, common.ids, common.account_id, common.clock,
        seam.trading_mode, seam.health.session_ok, seam.health.order_feed_ok,
    )  # fmt: skip
    always = JobScheduler(
        [
            Job("kill_switch", t.kill_switch, prelude.monitor.refresh),
            Job("fills", t.fill_sync, sync.sync),
            Job("resolution", t.resolution, engine.resolve_unresolved),
            Job("reconcile", t.reconcile, reconciler.run),
            Job("snapshot", t.snapshot.interval, _Intraday(snapshots)),
            *seam.extra_jobs,
            Job("events", t.events, _Drain(outbox, SystemEventRepository(db))),
            Job("worker_health", t.metrics, health_reports.report),
            *_tripwire_jobs(common, seam, prelude, broker, _Books(ledger, journal)),
        ],
        common.clock, alerts,
    )  # fmt: skip
    while_trading = JobScheduler(
        [Job("reprice", t.reprice, repricer.run_once)], common.clock, alerts
    )
    end_of_day = EndOfDay(sync, engine, reconciler, snapshots, alerts, common.close_out_hooks)
    host = StrategyHost(
        runs, common.bars, updates, journal, OrderUpdateTranslator(), factory, runs_board
    )
    results = CommandResultRepository(db)
    square_off = SquareOffService(ledger, journal, marks, sink, common.clock, alerts)
    processor = CommandProcessor(
        CommandRepository(db, common.commands_collection),
        results,
        _handlers(
            common, prelude.control, square_off, engine, sink, ledger, host, lifecycle,
            reconciler, results, StrategyRepository(db), seam.start_gate,
        ),
        common.clock, common.ids, alerts,
    )  # fmt: skip
    always.add(Job("commands", t.commands, _Commands(processor)))
    if t.quote_recording is not None:
        always.add(t.quote_recording.job(broker, in_flight, common.clock))
    telemetry = WorkerTelemetry(
        common.clock, lifecycle, journal, portfolio, marks, tick_rate, rejections, seam.health,
        prelude.monitor, tracker,
    )  # fmt: skip
    publisher = MetricsPublisher([telemetry], EmfMetricsSink(common.clock))
    always.add(Job("metrics", t.metrics, publisher.publish))
    worker = TradingWorker(
        lifecycle, t.schedule, common.clock, common.sleeper, alerts,
        seam.venue,
        StartupRecovery(engine, sync, reconciler, alerts),
        host,
        _SwitchView(prelude.monitor),
        always, while_trading,
        square_off,
        _Books(ledger, journal),
        end_of_day,
        _Flush(outbox, SystemEventRepository(db), seam.extra_flush),
    )  # fmt: skip
    return WorkerAssembly(
        worker, broker, engine, sync, reconciler, tracker, risk, sink, marks, book, outbox,
        lifecycle, ledger, journal, prelude.monitor, prelude.control, tuple(runs), host, processor,
        telemetry,
    )  # fmt: skip


def _tripwire_jobs(
    common: _CommonFields,
    seam: BrokerSeam,
    prelude: _Prelude,
    broker: Broker,
    orders: WorkingOrders,
) -> list[Job]:
    """The anomaly tripwire as a polled job, for paper and live workers alike so it is exercised
    in paper first. It halts through the kill switch as `tripwire`, which blocks new orders and
    lets exits through."""
    settings = common.tripwire
    if settings is None:
        return []
    rejections = RejectionTracker()
    broker.on_order_update(rejections.on_update)
    detectors: list[AnomalyDetector] = [
        UnresolvedUnknownOrderDetector(orders, settings.unknown_order),
        RejectionBurstDetector(
            rejections, settings.rejection_burst_count, settings.rejection_window
        ),
        OrderUpdateGapDetector(seam.health, orders, settings.order_feed_down),
    ]
    if common.feed_watch is not None:
        detectors[:0] = [
            FeedDroppedDetector(common.feed_watch, settings.feed_drop_halt),
            WidespreadStalenessDetector(common.feed_watch, settings.stale_fraction),
        ]
    tripwire = AnomalyTripwire(
        detectors,
        KillSwitchHalt(prelude.control, TRIPWIRE_SETTER),
        _SwitchView(prelude.monitor),
        common.clock,
        prelude.alerts,
    )
    return [Job("tripwire", settings.poll, tripwire.run_once)]


def _handlers(
    common: _CommonFields,
    control: KillSwitchControl,
    square_off: SquareOffService,
    engine: ExecutionEngine,
    sink: GatedExecutionSink,
    ledger: PlatformLedger,
    host: StrategyHost,
    lifecycle: SessionLifecycle,
    reconciler: Reconciler,
    results: CommandResultRepository,
    strategies: StrategyRepository,
    start_gate: StartGate,
) -> dict[CommandType, CommandHandler]:
    controller = StrategyController(host, common.config_validator or _NoValidator(), strategies)
    jobs = BackgroundJobs(common.job_runners, results, common.clock, common.ids)
    return {
        CommandType.SET_KILL_SWITCH: KillSwitchHandler(control),
        CommandType.SQUARE_OFF_ALL: SquareOffAllHandler(square_off),
        CommandType.CLOSE_POSITION: ClosePositionHandler(square_off),
        CommandType.CANCEL_ORDER: CancelOrderHandler(engine),
        CommandType.PLACE_MANUAL_ORDER: PlaceManualOrderHandler(sink, ledger, common.clock),
        CommandType.START_STRATEGY: StartStrategyHandler(controller, start_gate),
        CommandType.STOP_STRATEGY: StopStrategyHandler(controller),
        CommandType.UPDATE_STRATEGY_CONFIG: UpdateStrategyConfigHandler(controller, lifecycle),
        CommandType.TRIGGER_BACKFILL: BackfillHandler(jobs),
        CommandType.RUN_BACKTEST: BacktestHandler(jobs),
        CommandType.RECONCILE_NOW: ReconcileNowHandler(_ReconcileNow(reconciler)),
        CommandType.SET_TRADING_MODE: TradingModeHandler(),
    }


class _NoValidator:
    def validate(self, name: str, raw: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("strategy configuration cannot be validated in this deployment")


class _ReconcileNow:
    def __init__(self, reconciler: Reconciler) -> None:
        self._reconciler = reconciler

    async def run_now(self) -> int:
        return len(await self._reconciler.run(ReconciliationTrigger.MANUAL))


class _Commands:
    """Runs the command processor from the poll loop: recover once, then process what is pending."""

    def __init__(self, processor: CommandProcessor) -> None:
        self._processor = processor
        self._recovered = False

    async def __call__(self) -> object:
        if not self._recovered:
            self._recovered = True
            await self._processor.recover()
        return await self._processor.run_once()


class WarmupSource(Protocol):
    """The recent closed bars a strategy needs before its first live bar (indicator history)."""

    async def bars_for(self, config: ResolvedStrategyConfig) -> Sequence[Candle]: ...


class RepositoryWarmup:
    """Warm-up bars from the candle repository (the hot tier holds the last weeks): the same loader
    a backtest primes its strategy with, so a live run and a replay start from the same state."""

    def __init__(
        self,
        reader: CandleReader,
        clock: Clock,
        bars: int = DEFAULT_WARMUP_BARS,
        lookback: timedelta = DEFAULT_WARMUP_LOOKBACK,
    ) -> None:
        self._reader = reader
        self._clock = clock
        self._bars = bars
        self._lookback = lookback

    async def bars_for(self, config: ResolvedStrategyConfig) -> Sequence[Candle]:
        loader = WarmupLoader(
            self._reader, config.instrument_ids, config.timeframe, self._bars, self._lookback
        )
        return await loader.load(self._clock.now())


class _RunFactory:
    """Launches a run of a named strategy: records it, builds its runner, registers its pricing."""

    def __init__(
        self,
        launcher: StrategyRunLauncher,
        builder: StrategyRunnerBuilder,
        configs: Mapping[str, ResolvedStrategyConfig],
        session_date: str,
        clock: Clock,
        sink: GatedExecutionSink,
        book: StrategyPositionBook,
        alerts: AlertSink,
        buffers: dict[str, MarketableLimit],
        warmup: WarmupSource | None = None,
    ) -> None:
        self._launcher, self._builder, self._configs = launcher, builder, configs
        self._session_date, self._clock, self._sink = session_date, clock, sink
        self._book, self._alerts, self._buffers = book, alerts, buffers
        self._warmup = warmup

    async def launch(self, name: str) -> ManagedRun:
        config = self._configs.get(name)
        if config is None:
            raise ValueError(f"unknown strategy {name!r}")
        started = await self._launcher.start(config, self._session_date)
        prepared = self._builder.build(
            started,
            RunEnvironment(
                self._clock, WallClockSync(), self._sink, self._book.view_for(started.run_id),
                self._alerts,
            ),
        )  # fmt: skip
        if self._warmup is not None:
            for bar in await self._warmup.bars_for(config):
                prepared.history.record(bar)  # history only: warm-up bars never produce signals
        self._buffers[started.run_id] = MarketableLimit(config.execution.limit_buffer_bps)
        return ManagedRun(started.run_id, prepared.runner, name)


def bar_queue_for(configs: Sequence[ResolvedStrategyConfig]) -> ClosedBarQueue:
    return ClosedBarQueue(frozenset({c.timeframe for c in configs}) or frozenset({Timeframe.M5}))


def _reprice_policy(configs: Sequence[ResolvedStrategyConfig]) -> RepricePolicy:
    """One policy for the session: the most patient timing, the tightest chase, the fewest tries."""
    if not configs:
        return RepricePolicy(timedelta(seconds=30), 3, Decimal(20))
    return RepricePolicy(
        after=timedelta(seconds=min(c.execution.reprice_after_seconds for c in configs)),
        max_reprices=max(c.execution.max_reprices for c in configs),
        max_chase_bps=Decimal(50),
    )


class _RunLog:
    def __init__(self, runs: ReconciliationRunRepository) -> None:
        self._runs = runs

    async def record(self, record: Any) -> None:
        await self._runs.insert(record)


class _Intraday:
    def __init__(self, snapshots: SnapshotService) -> None:
        self._snapshots = snapshots

    async def __call__(self) -> object:
        from emporos.portfolio.snapshots import SnapshotKind

        return await self._snapshots.take(SnapshotKind.INTRADAY)


class _Drain:
    def __init__(self, outbox: EventOutbox, store: SystemEventRepository) -> None:
        self._outbox, self._store = outbox, store

    async def __call__(self) -> object:
        return await self._outbox.drain(self._store)


class _Flush:
    """Everything that must be durable before the process may end: fills, then events."""

    def __init__(
        self, outbox: EventOutbox, store: SystemEventRepository, extra: FlushExtra
    ) -> None:
        self._outbox, self._store, self._extra = outbox, store, extra

    async def flush(self) -> object:
        await self._extra.flush()
        return await self._outbox.drain(self._store)


class _SwitchView:
    def __init__(self, monitor: KillSwitchMonitor) -> None:
        self._monitor = monitor

    async def halted(self) -> bool:
        return (await self._monitor.refresh()).halted


class _Books:
    def __init__(self, ledger: PlatformLedger, journal: MongoOrderJournal) -> None:
        self._ledger, self._journal = ledger, journal

    async def open_positions(self) -> list[Any]:
        return await self._ledger.open_positions()

    async def active(self) -> list[Any]:
        return await self._journal.active()


async def stored_session_window(store: CalendarStore) -> SessionWindow:
    """The trading session as the exchange calendar stored in `market_calendar` says it is, so a
    holiday is not treated as an open session (EM-99 A4). Weekends are never trading days; a
    weekday the store has never heard of is assumed to trade, as before the calendar was seeded."""
    return SessionWindow(calendar=await StoredTradingCalendar.from_store(store))
