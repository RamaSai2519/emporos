"""One variant end to end on hand-built bars and a dictionary decider."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from emporos.eventtrader.llm.guards import ScopedTallies
from emporos.eventtrader.llm.pricing import PriceTable
from emporos.eventtrader.pipeline import PipelineDecision, Verdict
from emporos.eventtrader.posture import PostureSchedule
from emporos.eventtrader.replay.engine import Decider, PostureSource, ReplayEngine, TokenMeter
from emporos.eventtrader.replay.fills import ExitReason
from emporos.eventtrader.replay.report import VariantIdentity, render_report
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.runner import (
    DEV_WINDOW,
    CachedContext,
    DevWindowViolation,
    VariantRunner,
)
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
    decider: DictDecider,
    posture: PostureSchedule | None = None,
    runs: int = 20,
    calls: Meter | None = None,
) -> VariantRunner:
    m = make_market()

    def engines(d: Decider, p: PostureSource | None, t: TokenMeter | None) -> ReplayEngine:
        return ReplayEngine(d, NoContext(), m, RiskEngine(), COSTS, EXEC, posture=p, tokens=t)

    identity = VariantIdentity(
        "l1", "v1_t60", date(2024, 3, 4), date(2024, 3, 8), "hash", ("openai/gpt-4o-mini",)
    )
    return VariantRunner(
        identity, decider, engines, NoContext(), ScopedTallies(), PriceTable({}, D(88)),
        m.sessions, 60, posture, control_runs=runs, bootstrap_paths=50, calls=calls,
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


def test_the_snapshot_digest_changes_with_an_events_text_or_the_set_of_events() -> None:
    from emporos.eventtrader.runner import events_digest

    a, b = event(event_id="A", text="one"), event(event_id="B", text="two")

    assert events_digest([a, b]) == events_digest([a, b])
    assert events_digest([a, b]) != events_digest([a, event(event_id="B", text="three")])
    assert events_digest([a, b]).startswith("2-") and events_digest([a]) != events_digest([a, b])


class Meter:
    def __init__(self) -> None:
        self.hits, self.fresh = 0, 0


class MeteredDict(DictDecider):
    def __init__(self, answers: dict[str, PipelineDecision], meter: Meter) -> None:
        super().__init__(answers)
        self._meter = meter

    async def decide(self, item: EventInput) -> PipelineDecision:
        self._meter.fresh += 2
        self._meter.hits += 5
        return await super().decide(item)


async def test_a_run_reports_the_calls_it_made_against_those_the_journal_answered() -> None:
    meter = Meter()
    meter.hits, meter.fresh = 100, 100  # what earlier variants used is not this run's
    r = runner(MeteredDict(answers(), meter), calls=meter)
    outcome = await r.run(events(), with_control=False)

    assert (outcome.report.fresh_calls, outcome.report.journal_hits) == (6, 15)
    assert "6 fresh, 15 answered from the journal" in render_report(outcome.report)


async def test_no_decision_dated_after_the_dev_window_is_ever_asked_for() -> None:
    decider = DictDecider(answers())
    late = event(
        event_id="L1", published_at=at(date(2025, 1, 2), 10), usable_from=at(date(2025, 1, 2), 10)
    )

    with pytest.raises(DevWindowViolation, match="2025-01-02"):
        await runner(decider).run([*events(), late])

    assert decider.calls == 0  # refused before a single decision


async def test_the_window_edges_are_in_and_a_decision_pushed_past_midnight_is_out() -> None:
    edge = event(
        event_id="Z", instrument_id=X, published_at=at(date(2024, 12, 31), 15, 25),
        usable_from=at(date(2024, 12, 31), 15, 25),
    )  # fmt: skip
    past = event(
        event_id="P", instrument_id=X, published_at=at(date(2024, 12, 31), 23, 59),
        usable_from=at(date(2024, 12, 31), 23, 59),
    )  # fmt: skip

    DEV_WINDOW.check([edge])
    with pytest.raises(DevWindowViolation):
        DEV_WINDOW.check([past])  # decided at 00:01 on 2025-01-01
    with pytest.raises(DevWindowViolation, match="session"):
        DEV_WINDOW.check([], [date(2025, 1, 1)])
