"""`MultiStrategyBacktestEngine` — N strategies, one account, ranked and allocated per tick
(EM-152 / EM-158).

    N configs ──▶ N StrategyRuns ──▶ OpportunityPipeline.on_bars (ranked, allocated) ──▶ OrderFlow
                                            │                                              │
                                    BacktestSession ◀────────── fills ──────────────────────┘
                                            │
                        costs ─▶ BacktestPortfolio ─▶ MetricsCalculator (by_strategy) ─▶ Result

Reuses every piece of the single-strategy engine that is strategy-count-agnostic
(`SimulatedBroker`, `BacktestPortfolio`, `OrderFlow`, `BacktestSession`, `EventSettler`,
`BacktestCosts`): only how signals are PRODUCED differs. Bars for the same closed timestamp,
across every instrument any configured strategy watches, are delivered to `OpportunityPipeline`
together, so ranking and capital allocation see the whole universe at once — one strategy is never
evaluated in isolation of the others.

Scope, deliberately narrower than a single-strategy run: every configured strategy shares one
timeframe, one `session.square_off_at` and one `execution.limit_buffer_bps` — all three are really
account/session policy here, driving the one shared `ClosedBarFeed`, `SessionSquareOff` and
`OrderFlow` this engine builds, not a per-strategy choice. Configs that disagree are refused up
front (`ScopeError`), never silently reconciled.

Known limitations, not fixed here (the same shape `OpportunityRunner` already documents for
paper/live):

* a fill is not routed back to the strategy that caused it — `Strategy.on_order_update` is never
  called for a multi-strategy run. Every builtin strategy derives its state from its own indicator
  tracking (seeded once at `initialize()` from history) and from `ctx.positions`, never from
  `on_order_update`, so this is not a correctness bug for anything this repository ships; a
  strategy that DID rely on it would need order-update routing built first.
* likewise `Strategy.on_session_end` is never called (no builtin strategy overrides it); the
  broker's own end-of-day square-off still closes anything left open regardless.
* `ctx.positions` is the ACCOUNT's positions, not a strategy-private ledger — true of the
  single-strategy engine too, it just never mattered with only one strategy. Two strategies
  configured to trade the *same* instrument would see each other's position; `PortfolioAllocator`
  dedups by instrument before an entry ever reaches here, but a strategy that reads `ctx.positions`
  to size an EXIT could still act on another strategy's fill. Keep multi-strategy universes
  disjoint per instrument until this is solved (EM-99).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import localcontext

from emporos.backtest.alerts import CollectedAlerts
from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.clock import BarClock
from emporos.backtest.costs import BacktestCosts, CostSummary, ScheduleSource
from emporos.backtest.feed import ClosedBarFeed, FeedWindow, WarmupLoader
from emporos.backtest.flow import EventSettler, OrderEventQueue, OrderFlow, RunCounters
from emporos.backtest.metrics.breakdown import RegimeTimeline, build_regime_timeline
from emporos.backtest.metrics.decimal_math import CONTEXT
from emporos.backtest.metrics.report import MetricsCalculator, MetricsReport, MetricsSettings
from emporos.backtest.portfolio import BacktestPortfolio, ClosedTrade
from emporos.backtest.pricing import (
    GateContext,
    MarketableLimitPricing,
    PassThroughGate,
    SignalGate,
    TickSizes,
)
from emporos.backtest.session import BacktestSession
from emporos.backtest.settings import FillModelFactory, FillSettings
from emporos.backtest.square_off import ForcedClosePricing, SessionSquareOff
from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate
from emporos.opportunity.allocator import AllocationConstraints, PortfolioAllocator
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from emporos.opportunity.pipeline import OpportunityPipeline, RegimeSource, StrategyRun
from emporos.opportunity.scanner import OpportunityScanner
from emporos.persistence.candles import CandleReader
from emporos.risk.snapshot import AccountFacts
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import ClosedBarHistory
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.snapshot import ConfigSnapshotter

DEFAULT_WARMUP_BARS = 300
DEFAULT_WARMUP_LOOKBACK = timedelta(days=30)
REGIME_WARMUP_LOOKBACK = timedelta(days=120)


class ScopeError(ValueError):
    """The configured strategies disagree on something this engine treats as account policy."""


@dataclass(frozen=True)
class MultiStrategyBacktestSpec:
    configs: Sequence[ResolvedStrategyConfig]
    window: FeedWindow
    starting_cash: Money
    constraints: AllocationConstraints
    fills: FillSettings = field(default_factory=FillSettings)
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    warmup_bars: int = DEFAULT_WARMUP_BARS
    warmup_lookback: timedelta = DEFAULT_WARMUP_LOOKBACK
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.configs:
            raise ValueError("a multi-strategy backtest needs at least one strategy")
        self._shared("timeframe", (c.timeframe for c in self.configs))
        self._shared("square_off_at", (c.session.square_off_at for c in self.configs))
        self._shared("limit_buffer_bps", (c.execution.limit_buffer_bps for c in self.configs))

    @staticmethod
    def _shared(name: str, values: Iterable[Hashable]) -> None:
        seen = set(values)
        if len(seen) > 1:
            raise ScopeError(f"every strategy must share one {name}, got {seen}")


@dataclass(frozen=True)
class StrategyIdentity:
    """One configured strategy's identity in the result, index-aligned with `spec.configs`."""

    run_id: str
    strategy_name: str
    config_hash: str


@dataclass(frozen=True)
class MultiStrategyBacktestResult:
    strategies: tuple[StrategyIdentity, ...]
    spec: MultiStrategyBacktestSpec
    alerts: tuple[tuple[str, str], ...]
    counters: RunCounters
    costs: CostSummary
    risk_gate: str
    trades: tuple[ClosedTrade, ...]
    metrics: MetricsReport
    open_positions_at_end: int


class _TimelineRegimeSource:
    """Adapts a `RegimeTimeline` (already look-ahead safe, EM-119) to the `RegimeSource`
    protocol `OpportunityPipeline` expects, so its classification matches exactly what
    `by_regime` attributes trades to — one regime notion, not two. `MarketRegimeClassifier`
    itself is tuned for daily bars; feeding it this engine's intraday ticks directly would not
    be the same thing, so this reads the same precomputed daily timeline the metrics do."""

    def __init__(self, timeline: RegimeTimeline) -> None:
        self._timeline = timeline

    def update(self, candle: Candle) -> MarketRegime | None:
        value = self._timeline.at(candle.ts)
        return None if value is None else MarketRegime(value)


class _NoOpReceiver:
    """`EventSettler` needs an `UpdateReceiver`; a multi-strategy run does not route fills back
    to `Strategy.on_order_update` (see the module docstring's known limitations)."""

    async def handle_order_update(self, update: OrderUpdate) -> None:
        return None


@dataclass(frozen=True)
class _PreparedStrategy:
    """Everything built for one configured strategy before the replay starts."""

    identity: StrategyIdentity
    runs: tuple[StrategyRun, ...]


class MultiStrategyBacktestEngine:
    def __init__(
        self,
        reader: CandleReader,
        registry: StrategyRegistry,
        ticks: TickSizes,
        schedules: Callable[[], ScheduleSource],
        gate: Callable[[GateContext], SignalGate] = PassThroughGate,
        fill_models: FillModelFactory | None = None,
        snapshotter: ConfigSnapshotter | None = None,
        metrics: Callable[[MetricsSettings], MetricsCalculator] = MetricsCalculator,
        allocator: PortfolioAllocator | None = None,
        jev_filter: JevMetaDecisionFilter | None = None,
    ) -> None:
        self._reader = reader
        self._registry = registry
        self._ticks = ticks
        self._schedules = schedules
        self._gate = gate
        self._fill_models = fill_models or FillModelFactory()
        self._snapshotter = snapshotter or ConfigSnapshotter()
        self._metrics = metrics
        self._allocator = allocator or PortfolioAllocator()
        self._jev_filter = jev_filter

    async def run(self, spec: MultiStrategyBacktestSpec) -> MultiStrategyBacktestResult:
        """One run, under the backtest's own decimal context (mirrors `BacktestEngine.run`)."""
        with localcontext(CONTEXT):
            return await self._run(spec)

    async def _run(self, spec: MultiStrategyBacktestSpec) -> MultiStrategyBacktestResult:
        first_day = spec.window.start.astimezone(IST).date()
        timeframe = spec.configs[0].timeframe
        clock = BarClock(spec.window.start)
        counters, queue = RunCounters(), OrderEventQueue()
        broker = SimulatedBroker(
            clock, self._fill_models.model(spec.fills), self._fill_models.rejects(spec.fills)
        )
        portfolio = BacktestPortfolio(spec.starting_cash)
        costs = BacktestCosts(self._schedules())
        gate = self._gate(GateContext(clock.view(), portfolio, broker))
        alerts = CollectedAlerts()

        instrument_ids = sorted({i for c in spec.configs for i in c.instrument_ids})
        daily_bars = await self._daily_bars(instrument_ids, spec.window)
        regime_timelines = {
            instrument_id: build_regime_timeline(bars) for instrument_id, bars in daily_bars.items()
        }
        regimes: dict[str, RegimeSource] = {
            instrument_id: _TimelineRegimeSource(timeline)
            for instrument_id, timeline in regime_timelines.items()
        }

        prepared: list[_PreparedStrategy] = []
        for config in spec.configs:
            prepared.append(await self._prepare(config, first_day, clock, portfolio, spec))

        pipeline = OpportunityPipeline(
            runs=[run for p in prepared for run in p.runs],
            regimes=regimes,
            scanner=OpportunityScanner(self._registry),
            allocator=self._allocator,
            account=lambda: self._account_facts(portfolio),
            constraints=spec.constraints,
            jev_filter=self._jev_filter,
            alerts=alerts,
        )
        square_off = SessionSquareOff(spec.configs[0].session.square_off_at, portfolio.owner, clock)
        flow = OrderFlow(
            broker, gate,
            MarketableLimitPricing(spec.configs[0].execution.limit_buffer_bps, self._ticks),
            square_off, queue, counters,
        )  # fmt: skip
        session = BacktestSession(
            broker, portfolio, flow, queue,
            EventSettler(queue, costs, portfolio, _NoOpReceiver(), counters),
            square_off, ForcedClosePricing(spec.fills.forced_close_penalty_bps), counters,
            portfolio.owner,
        )  # fmt: skip

        feed = ClosedBarFeed(self._reader, instrument_ids, timeframe, spec.window)
        await _MultiStrategyReplay(session, pipeline, flow, clock).run(feed)

        return MultiStrategyBacktestResult(
            strategies=tuple(p.identity for p in prepared),
            spec=spec,
            alerts=alerts.alerts,
            counters=counters,
            costs=costs.summary(),
            risk_gate=gate.name,
            trades=portfolio.closed_trades,
            metrics=self._metrics(spec.metrics).calculate(
                spec.starting_cash,
                portfolio.equity_curve,
                portfolio.closed_trades,
                portfolio.traded_notional,
                regime_timelines,
            ),
            open_positions_at_end=len(portfolio.open_positions()),
        )

    async def _prepare(
        self,
        config: ResolvedStrategyConfig,
        first_day: date,
        clock: BarClock,
        portfolio: BacktestPortfolio,
        spec: MultiStrategyBacktestSpec,
    ) -> _PreparedStrategy:
        snapshot = self._snapshotter.take(config)
        run_id = (
            f"bt-{snapshot.content_hash.split(':', 1)[-1][:16]}-{first_day:%Y%m%d}-{config.name}"
        )
        history = ClosedBarHistory(clock.view())
        context = StrategyContext(
            run_id=run_id,
            config=config,
            clock=clock.view(),
            logger=logging.getLogger(f"emporos.strategy.{config.name}"),
            history=history,
            positions=portfolio,
            rng=random.Random(snapshot.content_hash),
        )
        warmup = WarmupLoader(
            self._reader, config.instrument_ids, config.timeframe, spec.warmup_bars,
            spec.warmup_lookback,
        )  # fmt: skip
        for bar in await warmup.load(spec.window.start):
            history.record(bar)
        strategy = self._registry.create(config)
        strategy.initialize(context)
        runs = tuple(
            StrategyRun(config.name, instrument_id, config.timeframe, strategy, config.risk)
            for instrument_id in config.instrument_ids
        )
        return _PreparedStrategy(StrategyIdentity(run_id, config.name, snapshot.content_hash), runs)

    @staticmethod
    def _account_facts(portfolio: BacktestPortfolio) -> AccountFacts:
        return AccountFacts(positions={p.instrument_id: p for p in portfolio.open_positions()})

    async def _daily_bars(
        self, instrument_ids: Sequence[str], window: FeedWindow
    ) -> dict[str, list[Candle]]:
        start = window.start - REGIME_WARMUP_LOOKBACK
        bars_by_instrument = await asyncio.gather(
            *(
                self._reader.get_range(instrument_id, Timeframe.D1, start, window.end)
                for instrument_id in instrument_ids
            )
        )
        return dict(zip(instrument_ids, bars_by_instrument, strict=True))


class _MultiStrategyReplay:
    """`BarReplay` for N strategies: bars sharing a timestamp are batched before being handed to
    `OpportunityPipeline`, but every bar still settles the broker one at a time, exactly like the
    single-strategy engine (`BacktestSession.before_bar` is unchanged and per-instrument)."""

    def __init__(
        self,
        session: BacktestSession,
        pipeline: OpportunityPipeline,
        flow: OrderFlow,
        clock: BarClock,
    ) -> None:
        self._session = session
        self._pipeline = pipeline
        self._flow = flow
        self._clock = clock

    async def run(self, feed: ClosedBarFeed) -> None:
        day: date | None = None
        batch: list[Candle] = []
        async for bar in feed:
            bar_day = bar.ts.astimezone(IST).date()
            if day is not None and bar_day != day:
                await self._flush(batch)
                batch = []
                await self._close_session(day)
            day = bar_day
            if batch and batch[0].ts != bar.ts:
                await self._flush(batch)
                batch = []
            self._sync(bar.closes_at)
            await self._session.before_bar(bar)
            batch.append(bar)
        await self._flush(batch)
        if day is not None:
            await self._close_session(day)

    def _sync(self, when: datetime) -> None:
        if when > self._clock.now():
            self._clock.set(when)

    async def _flush(self, batch: list[Candle]) -> None:
        if not batch:
            return
        outcome = await self._pipeline.on_bars(batch)
        for signal in (*outcome.exits, *outcome.approved_signals):
            await self._flow.submit(signal)

    async def _close_session(self, day: date) -> None:
        await self._session.session_ending(day)
        await self._session.session_closed(day)
