"""One variant end to end on hand-built bars and a dictionary decider."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from emporos.eventtrader.llm.guards import ScopedTallies
from emporos.eventtrader.llm.pricing import PriceTable
from emporos.eventtrader.pipeline import PipelineDecision, Verdict
from emporos.eventtrader.posture import PostureSchedule
from emporos.eventtrader.replay.engine import Decider, PostureSource, ReplayEngine, TokenMeter
from emporos.eventtrader.replay.fills import ExitReason
from emporos.eventtrader.replay.report import VariantIdentity
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.runner import CachedContext, VariantRunner
from emporos.eventtrader.stages.models import (
    Horizon,
    Posture,
    TriageDirection,
    TriageResult,
)
from emporos.eventtrader.stages.stages import EventInput
from tests.unit.eventtrader.fakes import NOON, event
from tests.unit.eventtrader.replay.fakes import MON, at, flat_day, replace_bar
from tests.unit.eventtrader.replay.test_engine import (
    COSTS,
    EXEC,
    NoContext,
    ev,
    market,
    plan,
)

D = Decimal
X, Y = "NSE:2885", "NSE:1594"


def triaged(decision: PipelineDecision) -> PipelineDecision:
    triage = TriageResult(True, TriageDirection.UP, 3.0, Horizon.INTRADAY, False, 80, "r")
    return PipelineDecision(decision.event_id, decision.verdict, decision.plan, triage=triage)


class DictDecider:
    def __init__(self, answers: dict[str, PipelineDecision]) -> None:
        self._answers = answers
        self.calls = 0

    async def decide(self, item: EventInput) -> PipelineDecision:
        self.calls += 1
        return self._answers[item.event.event_id]


def make_market():  # type: ignore[no-untyped-def]
    m = market()
    for name in (X, Y):
        m.put_day(
            name, MON, replace_bar(flat_day(name, MON), 12, 5, o=100, h=106.5, low=100, c=106)
        )
    return m


def runner(
    decider: DictDecider, posture: PostureSchedule | None = None, runs: int = 20
) -> VariantRunner:
    m = make_market()

    def engines(d: Decider, p: PostureSource | None, t: TokenMeter | None) -> ReplayEngine:
        return ReplayEngine(d, NoContext(), m, RiskEngine(), COSTS, EXEC, posture=p, tokens=t)

    identity = VariantIdentity(
        "l1", "v1_t60", date(2024, 3, 4), date(2024, 3, 8), "hash", ("openai/gpt-4o-mini",)
    )
    return VariantRunner(
        identity, decider, engines, NoContext(), ScopedTallies(), PriceTable({}, D(88)),
        m.sessions, 60, posture, control_runs=runs, bootstrap_paths=50,
    )  # fmt: skip


def events():  # type: ignore[no-untyped-def]
    return [
        ev("E1"),
        ev("E2", name=Y, at_=NOON + timedelta(minutes=1)),
        ev("E3", at_=NOON + timedelta(hours=1)),
        ev("E4", name=""),
    ]


def answers() -> dict[str, PipelineDecision]:
    no = PipelineDecision("E3", Verdict.JUDGE_NO)
    return {
        "E1": triaged(PipelineDecision("E1", Verdict.TRADE, plan(stop=3.0, target=6.0))),
        "E2": triaged(PipelineDecision("E2", Verdict.TRADE, plan(stop=2.0, target=6.0))),
        "E3": triaged(no),
    }


async def test_a_variant_is_decided_once_replayed_and_reported_with_control_and_baseline() -> None:
    decider = DictDecider(answers())
    outcome = await runner(decider).run(events())

    report = outcome.report
    assert decider.calls == 3  # the market-wide item never reaches the decider
    assert [t.exit_reason for t in report.result.trades] == [ExitReason.TARGET] * 2
    assert len(report.bars) == 9 and report.control is not None and report.control.runs == 20
    assert report.baseline is not None and "intraday: stop 2.5%" in report.baseline_rule
    assert report.benchmark.trades == 2
    assert set(outcome.decisions.decisions()) == {"E1", "E2", "E3"}


async def test_the_control_compares_trading_net_so_the_shared_token_cost_cancels() -> None:
    outcome = await runner(DictDecider(answers())).run(events())

    real = sum((t.net_benchmark for t in outcome.report.result.trades), D(0))
    assert outcome.report.control is not None and outcome.report.control.real_net == real


async def test_the_posture_scales_the_budgets_and_its_cost_lands_on_its_day() -> None:
    hold = PostureSchedule({MON: D(0)}, {MON: D(7)}, {}, {MON: Posture.HOLD})
    outcome = await runner(DictDecider(answers()), hold).run(events(), with_control=False)

    result = outcome.report.result
    assert result.trades == () and result.stats.refused["posture_hold"] == 2
    assert result.token_cost_inr == D(7) and result.stats.token_cost_by_day[MON] == D(7)
    assert outcome.report.control is None


def test_the_cached_context_builds_each_events_numbers_once() -> None:
    class Counting(NoContext):
        n = 0

        def context(self, e, at_):  # type: ignore[no-untyped-def]
            Counting.n += 1
            return super().context(e, at_)

    cached = CachedContext(Counting())
    e = event()
    cached.context(e, at(MON, 12))
    cached.context(e, at(MON, 12))

    assert Counting.n == 1
