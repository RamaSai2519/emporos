"""`BacktestEngine` — one deterministic backtest run (plan.md §10).

    candles ─▶ ClosedBarFeed ─▶ BarReplay ─▶ StrategyRunner ─▶ OrderFlow ─▶ SimulatedBroker
                                     │                                            │
                              BacktestSession ◀───────── fills ◀──────────────────┘
                                     │
                     costs ─▶ BacktestPortfolio ─▶ MetricsCalculator ─▶ BacktestResult

The same input gives the same result: the clock is the bar clock, randomness is seeded, ids are
counters, and nothing reads the wall clock. There is NO risk engine yet (Phase 11): the gate is a
pass-through and every result says so. The engine is handed its collaborators (reader, registry,
cost and gate factories); the objects that hold ONE run's state (clock, broker, book) are made
per run, here, and never shared between runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import localcontext

from emporos.backtest.alerts import CollectedAlerts
from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.clock import BarClock
from emporos.backtest.costs import BacktestCosts, CostSummary, ScheduleSource
from emporos.backtest.feed import ClosedBarFeed, FeedWindow, WarmupLoader
from emporos.backtest.flow import EventSettler, OrderEventQueue, OrderFlow, RunCounters
from emporos.backtest.metrics.breakdown import build_regime_timeline
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
from emporos.backtest.progress import BacktestProgressSink, NullBacktestProgressSink
from emporos.backtest.provenance import ResearchProvenance
from emporos.backtest.replay import BarReplay
from emporos.backtest.session import BacktestSession
from emporos.backtest.settings import FillModelFactory, FillSettings
from emporos.backtest.square_off import ForcedClosePricing, SessionSquareOff
from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.candles import CandleReader
from emporos.session.strategy_runs import RunEnvironment, StartedRun, StrategyRunnerBuilder
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.runner import ReplayClockSync, RunReport
from emporos.strategies.snapshot import ConfigSnapshotter

DEFAULT_WARMUP_BARS = 300
DEFAULT_WARMUP_LOOKBACK = timedelta(days=30)
# Covers the regime classifier's own warm-up (a trailing 60 trading-day volatility window) with
# room for weekends and holidays; the classifier itself has no opinion about calendar days.
REGIME_WARMUP_LOOKBACK = timedelta(days=120)


@dataclass(frozen=True)
class BacktestSpec:
    config: ResolvedStrategyConfig
    window: FeedWindow
    starting_cash: Money
    fills: FillSettings = field(default_factory=FillSettings)
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    warmup_bars: int = DEFAULT_WARMUP_BARS
    warmup_lookback: timedelta = DEFAULT_WARMUP_LOOKBACK
    assumptions: tuple[str, ...] = ()  # things the caller assumed (e.g. the universe basis)
    # What dataset/universe/calendar/quarantine this ran against (EM-177); None from a caller that
    # has not been wired to a calendar/quarantine store yet.
    provenance: ResearchProvenance | None = None


@dataclass(frozen=True)
class BacktestResult:
    run_id: str
    config_hash: str
    spec: BacktestSpec
    runner: RunReport
    alerts: tuple[tuple[str, str], ...]
    counters: RunCounters
    costs: CostSummary
    risk_gate: str
    trades: tuple[ClosedTrade, ...]
    metrics: MetricsReport
    open_positions_at_end: int


class BacktestEngine:
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
        progress: BacktestProgressSink | None = None,
    ) -> None:
        self._reader = reader
        self._registry = registry
        self._ticks = ticks
        self._schedules = schedules
        self._gate = gate
        self._fill_models = fill_models or FillModelFactory()
        self._snapshotter = snapshotter or ConfigSnapshotter()
        self._metrics = metrics
        self._progress: BacktestProgressSink = progress or NullBacktestProgressSink()

    async def run(self, spec: BacktestSpec) -> BacktestResult:
        """One run, under the backtest's own decimal context: prices, charges and averages must
        not depend on whatever context the caller happens to have (decimal contexts are per
        task, so this covers everything the run awaits)."""
        with localcontext(CONTEXT):
            return await self._run(spec)

    async def _run(self, spec: BacktestSpec) -> BacktestResult:
        config = spec.config
        snapshot = self._snapshotter.take(config)
        first_day = spec.window.start.astimezone(IST).date()
        run_id = f"bt-{snapshot.content_hash.split(':', 1)[-1][:16]}-{first_day:%Y%m%d}"

        clock = BarClock(spec.window.start)
        counters, queue = RunCounters(), OrderEventQueue()
        broker = SimulatedBroker(
            clock, self._fill_models.model(spec.fills), self._fill_models.rejects(spec.fills)
        )
        portfolio = BacktestPortfolio(spec.starting_cash)
        costs = BacktestCosts(self._schedules())
        gate = self._gate(GateContext(clock.view(), portfolio, broker))
        square_off = SessionSquareOff(config.session.square_off_at, lambda _id: run_id, clock)
        flow = OrderFlow(
            broker, gate, MarketableLimitPricing(config.execution.limit_buffer_bps, self._ticks),
            square_off, queue, counters,
        )  # fmt: skip
        alerts = CollectedAlerts()
        prepared = StrategyRunnerBuilder(self._registry).build(
            StartedRun(run_id, config, snapshot, first_day.isoformat()),
            RunEnvironment(clock.view(), ReplayClockSync(clock), flow, portfolio, alerts),
        )
        warmup = WarmupLoader(
            self._reader, config.instrument_ids, config.timeframe, spec.warmup_bars,
            spec.warmup_lookback,
        )  # fmt: skip
        for bar in await warmup.load(spec.window.start):
            prepared.history.record(bar)
        daily_bars = await self._daily_bars(config.instrument_ids, spec.window)
        for bars in daily_bars.values():
            for bar in bars:
                prepared.history.record(bar)  # look-ahead safe: bars() still gates on closes_at

        session = BacktestSession(
            broker, portfolio, flow, queue,
            EventSettler(queue, costs, portfolio, prepared.runner, counters),
            square_off,
            ForcedClosePricing(spec.fills.forced_close_penalty_bps),
            counters,
            lambda _instrument_id: run_id,
            self._progress,
        )  # fmt: skip
        feed = ClosedBarFeed(self._reader, config.instrument_ids, config.timeframe, spec.window)
        report = await BarReplay(prepared.runner, clock, session).run(feed)
        regime_timelines = {
            instrument_id: build_regime_timeline(bars) for instrument_id, bars in daily_bars.items()
        }

        return BacktestResult(
            run_id=run_id,
            config_hash=snapshot.content_hash,
            spec=spec,
            runner=report,
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

    async def _daily_bars(
        self, instrument_ids: Sequence[str], window: FeedWindow
    ) -> dict[str, list[Candle]]:
        """Every daily bar per traded instrument across the run, with enough lookback before the
        window for the regime classifier's own warm-up. Reads through the same `CandleReader`
        everything else in this engine does — never a second path to candle data. Recording all of
        it (not just a warm-up prefix) into the strategy's `BarHistory` is look-ahead safe: `bars()`
        still gates on each candle's `closes_at` against the replay clock, so a strategy reading
        `Timeframe.D1` sees a day's bar exactly when that day has actually closed, never sooner."""
        start = window.start - REGIME_WARMUP_LOOKBACK
        bars_by_instrument = await asyncio.gather(
            *(
                self._reader.get_range(instrument_id, Timeframe.D1, start, window.end)
                for instrument_id in instrument_ids
            )
        )
        return dict(zip(instrument_ids, bars_by_instrument, strict=True))
