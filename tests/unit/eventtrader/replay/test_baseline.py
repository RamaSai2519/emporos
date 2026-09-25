"""The triage-only baseline: triage's direction, the variant's own median stop, target and hold."""

from __future__ import annotations

from emporos.eventtrader.pipeline import PipelineDecision, TradePlan, Verdict
from emporos.eventtrader.replay.baseline import TriageOnlyBaseline, horizon_rules
from emporos.eventtrader.stages.models import (
    Horizon,
    Instrument,
    Side,
    TriageDirection,
    TriageResult,
)
from tests.unit.eventtrader.fakes import item


def triage(
    direction: TriageDirection = TriageDirection.UP, horizon: Horizon = Horizon.SWING,
    confidence: int = 80, material: bool = True,
) -> TriageResult:  # fmt: skip
    return TriageResult(material, direction, 3.0, horizon, False, confidence, "r")


def approved(
    eid: str, instrument: Instrument, stop: float, target: float, hold: int
) -> PipelineDecision:
    plan = TradePlan(instrument, Side.LONG, stop, target, hold, 75)
    return PipelineDecision(eid, Verdict.TRADE, plan, triage=triage())


def variant() -> dict[str, PipelineDecision]:
    decisions = [
        approved("A", Instrument.CASH_INTRADAY, 1.0, 2.0, 0),
        approved("B", Instrument.CASH_INTRADAY, 3.0, 6.0, 0),
        approved("C", Instrument.CASH_INTRADAY, 2.0, 5.0, 0),
        approved("D", Instrument.CALL, 4.0, 8.0, 5),
        approved("E", Instrument.CASH_SWING, 6.0, 10.0, 9),
    ]
    return {d.event_id: d for d in decisions}


def recorded(eid: str, **kw: object) -> dict[str, PipelineDecision]:
    return {**variant(), eid: PipelineDecision(eid, Verdict.JUDGE_NO, triage=triage(**kw))}  # type: ignore[arg-type]


def test_the_rules_are_the_medians_of_each_horizons_approved_trades() -> None:
    rules = horizon_rules(variant().values())

    assert (rules[Horizon.INTRADAY].stop_pct, rules[Horizon.INTRADAY].target_pct) == (2.0, 5.0)
    assert rules[Horizon.INTRADAY].hold_days == 0
    assert (rules[Horizon.SWING].stop_pct, rules[Horizon.SWING].target_pct) == (5.0, 9.0)
    assert rules[Horizon.SWING].hold_days == 7


async def test_an_up_swing_is_a_cash_long_and_a_down_swing_is_the_put() -> None:
    up = TriageOnlyBaseline(recorded("X"), 60)
    down = TriageOnlyBaseline(recorded("X", direction=TriageDirection.DOWN), 60)

    plan = (await up.decide(item(event_id="X"))).plan
    assert plan is not None and (plan.instrument, plan.side) == (Instrument.CASH_SWING, Side.LONG)
    assert (plan.stop_pct, plan.target_pct, plan.hold_days, plan.confidence) == (5.0, 9.0, 7, 80)
    put = (await down.decide(item(event_id="X"))).plan
    assert put is not None and (put.instrument, put.side) == (Instrument.PUT, Side.LONG)


async def test_a_down_intraday_is_a_short() -> None:
    decisions = recorded("X", direction=TriageDirection.DOWN, horizon=Horizon.INTRADAY)
    plan = (await TriageOnlyBaseline(decisions, 60).decide(item(event_id="X"))).plan

    assert plan is not None and (plan.instrument, plan.side) == (
        Instrument.CASH_INTRADAY,
        Side.SHORT,
    )
    assert plan.hold_days == 0


async def test_what_triage_would_have_stopped_is_not_traded() -> None:
    cases = {
        "not material": ({"material": False}, Verdict.NOT_MATERIAL),
        "no horizon": ({"horizon": Horizon.NONE}, Verdict.NO_HORIZON),
        "no direction": ({"direction": TriageDirection.NONE}, Verdict.NO_HORIZON),
        "below threshold": ({"confidence": 59}, Verdict.BELOW_THRESHOLD),
    }
    for kw, verdict in cases.values():
        baseline = TriageOnlyBaseline(recorded("X", **kw), 60)
        assert (await baseline.decide(item(event_id="X"))).verdict is verdict


async def test_an_unknown_event_and_a_horizon_with_no_reference_trades_are_not_traded() -> None:
    baseline = TriageOnlyBaseline(recorded("X"), 60, rules={})

    assert (await baseline.decide(item(event_id="X"))).verdict is Verdict.NO_HORIZON
    assert (await baseline.decide(item(event_id="nope"))).verdict is Verdict.NOT_MATERIAL
    assert baseline.rules == {}
