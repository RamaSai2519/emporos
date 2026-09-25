"""One Dev variant, end to end (EM-240): decide every event up front, walk the replay, and produce
the report (PROFIT_PLAN §12.5).

Everything concrete is injected: the pipeline (a `Decider`), the engine factory (market, risk
engine, costs, options placer), the posture schedule, the events and the sessions. The runner only
orders the steps and applies the rules that are not a component's to bend:

* the events the decider sees are the run's own, in order; the replay reads the recorded answers;
* the posture's token cost is charged to the day it was spent for, beside the decisions';
* the coin-flip control compares TRADING net (token cost is the same in both arms, so it cancels)
  at the benchmark scenario;
* the triage-only baseline is a diagnostic: it reads the variant's own recorded triage answers, so
  it costs no calls, and never enters a bar."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from emporos.eventtrader.events import ContextBuilder, MarketContext, MarketEvent
from emporos.eventtrader.llm.guards import ScopedTallies
from emporos.eventtrader.llm.pricing import PriceTable
from emporos.eventtrader.posture import PostureSchedule
from emporos.eventtrader.prefetch import DecisionPrefetcher, PrefetchedDecisions
from emporos.eventtrader.replay.baseline import TriageOnlyBaseline
from emporos.eventtrader.replay.control import CoinFlipControl, ControlResult
from emporos.eventtrader.replay.engine import (
    Decider,
    PostureSource,
    ReplayEngine,
    RunResult,
    TokenMeter,
    decision_input,
)
from emporos.eventtrader.replay.metrics import (
    LossBootstrap,
    ScenarioMetrics,
    Section125Bars,
    metrics_for,
)
from emporos.eventtrader.replay.records import Scenario
from emporos.eventtrader.replay.report import VariantIdentity, VariantReport

__all__ = ["CachedContext", "EngineFactory", "VariantRunner"]

EngineFactory = Callable[[Decider, PostureSource | None, TokenMeter | None], ReplayEngine]


class CachedContext:
    """Builds each event's context once: the prefetch, the replay and the baseline ask for the same
    numbers, and a decision time is a function of the event alone."""

    def __init__(self, inner: ContextBuilder) -> None:
        self._inner = inner
        self._held: dict[tuple[str, datetime], MarketContext] = {}

    def context(self, event: MarketEvent, decision_at: datetime) -> MarketContext:
        key = (event.event_id, decision_at)
        if key not in self._held:
            self._held[key] = self._inner.context(event, decision_at)
        return self._held[key]


@dataclass(frozen=True)
class VariantOutcome:
    report: VariantReport
    decisions: PrefetchedDecisions


class VariantRunner:
    def __init__(
        self,
        identity: VariantIdentity,
        decider: Decider,
        engines: EngineFactory,
        context: ContextBuilder,
        scopes: ScopedTallies,
        prices: PriceTable,
        sessions: Sequence[date],
        triage_threshold: int,
        posture: PostureSchedule | None = None,
        concurrency: int = 8,
        control_runs: int = 1000,
        bootstrap_paths: int = 10_000,
    ) -> None:
        self._identity, self._decider, self._engines = identity, decider, engines
        self._context = CachedContext(context)
        self._scopes, self._prices, self._sessions = scopes, prices, tuple(sessions)
        self._threshold, self._posture = triage_threshold, posture
        self._concurrency, self._control_runs = concurrency, control_runs
        self._bootstrap = LossBootstrap(paths=bootstrap_paths)

    async def run(self, events: Sequence[MarketEvent], with_control: bool = True) -> VariantOutcome:
        named = [e for e in events if e.instrument_id]
        items = [decision_input(e, self._context) for e in named]
        prefetch = DecisionPrefetcher(self._decider, self._scopes, self._prices, self._concurrency)
        decided = await prefetch.run(items)
        result = await self._engines(decided, self._posture, decided).run(events)
        result = self._with_posture_cost(result)
        benchmark = metrics_for(result, Scenario.BENCHMARK, self._sessions)
        adverse = metrics_for(result, Scenario.ADVERSE, self._sessions)
        p_loss = self._bootstrap.p_loss([v for _, v in benchmark.daily])
        control = await self._control(events, decided, result) if with_control else None
        bars = Section125Bars().evaluate(
            benchmark, adverse, p_loss, control.p_value if control else None
        )
        baseline, rule = await self._baseline(events, decided)
        report = VariantReport(
            self._identity, result, benchmark, adverse, p_loss, tuple(bars), control, baseline, rule
        )
        return VariantOutcome(report, decided)

    def _with_posture_cost(self, result: RunResult) -> RunResult:
        if self._posture is None:
            return result
        for day, cost in self._posture.cost_by_day.items():
            result.stats.token_cost_by_day[day] += cost
        return replace(result, token_cost_inr=result.token_cost_inr + self._posture.total_cost_inr)

    async def _control(
        self, events: Sequence[MarketEvent], decided: PrefetchedDecisions, real: RunResult
    ) -> ControlResult:
        control = CoinFlipControl(
            lambda coin: self._engines(coin, self._posture, None),
            decided.decisions(),
            runs=self._control_runs,
        )
        trading = sum((t.net(Scenario.BENCHMARK) for t in real.trades), Decimal(0))
        return await control.run(events, trading, Scenario.BENCHMARK)

    async def _baseline(
        self, events: Sequence[MarketEvent], decided: PrefetchedDecisions
    ) -> tuple[ScenarioMetrics | None, str]:
        baseline = TriageOnlyBaseline(decided.decisions(), self._threshold)
        rules = baseline.rules
        rule = "; ".join(
            f"{h.value}: stop {r.stop_pct:g}%, target {r.target_pct:g}%, hold {r.hold_days}d"
            for h, r in sorted(rules.items())
        )
        if not rules:
            return None, "the variant approved no trade, so there is no reference"
        chosen = [e for e in events if baseline.would_trade(e.event_id)]
        result = await self._engines(baseline, self._posture, None).run(chosen)
        return metrics_for(result, Scenario.BENCHMARK, self._sessions), rule
