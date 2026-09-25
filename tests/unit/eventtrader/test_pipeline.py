"""EM-240: the decision pipeline's ordering and the rules that are not the models' to bend."""

from __future__ import annotations

import pytest

from emporos.eventtrader.pipeline import DecisionPipeline, PipelineConfig, Verdict
from emporos.eventtrader.stages.models import Horizon, Instrument, Side
from emporos.eventtrader.stages.stages import (
    ArbiterStage,
    JudgeStage,
    PanelistStage,
    TriageStage,
)
from tests.unit.eventtrader.fakes import (
    TRIAGE_OK,
    ScriptedLlm,
    item,
    judge,
    reply,
    transport_error,
    view,
)

MODEL = "openai/gpt-4o-mini"


def pipeline(
    client: ScriptedLlm, *, threshold: int = 60, panel: bool = True, arbiter: bool = False
) -> DecisionPipeline:
    return DecisionPipeline(
        PipelineConfig(threshold, panel, arbiter),
        TriageStage(client, MODEL),
        (
            PanelistStage.bull(client, MODEL),
            PanelistStage.bear(client, MODEL),
            PanelistStage.tape(client, MODEL),
        ),
        JudgeStage(client, MODEL),
        ArbiterStage(client, "gpt-4o-2024-08-06") if arbiter else None,
    )


def triage(**fields: object) -> str:
    base = {
        "material": True,
        "direction": "up",
        "expected_move_pct": 3.0,
        "horizon": "swing",
        "priced_in": False,
        "confidence": 80,
        "reason": "x",
    }
    return reply(**{**base, **fields})


async def test_agreement_of_two_panellists_and_the_judge_gives_a_trade_plan() -> None:
    client = ScriptedLlm(
        triage=[TRIAGE_OK],
        bull=[view("long")],
        bear=[view("short")],
        tape=[view("long")],
        judge=[judge()],
    )

    decision = await pipeline(client).decide(item())

    assert decision.verdict is Verdict.TRADE and decision.plan is not None
    assert (decision.plan.instrument, decision.plan.side) == (Instrument.CASH_SWING, Side.LONG)
    assert (decision.plan.stop_pct, decision.plan.target_pct, decision.plan.hold_days) == (
        3.0,
        6.0,
        5,
    )
    assert len(decision.views) == 3 and decision.judge is not None and decision.arbiter is None


async def test_one_panellist_on_the_judges_side_is_not_enough() -> None:
    client = ScriptedLlm(
        triage=[TRIAGE_OK],
        bull=[view("long")],
        bear=[view("short")],
        tape=[view("none", "none", 0)],
        judge=[judge()],
    )

    decision = await pipeline(client).decide(item())

    assert decision.verdict is Verdict.PANEL_DISAGREES and decision.plan is None


async def test_the_judge_saying_no_ends_it_even_when_the_panel_agrees() -> None:
    client = ScriptedLlm(
        triage=[TRIAGE_OK], bull=[view()], bear=[view()], tape=[view()], judge=[judge(trade=False)]
    )

    assert (await pipeline(client).decide(item())).verdict is Verdict.JUDGE_NO


@pytest.mark.parametrize(
    ("fields", "verdict"),
    [
        ({"material": False}, Verdict.NOT_MATERIAL),
        ({"horizon": "none"}, Verdict.NO_HORIZON),
        ({"confidence": 59}, Verdict.BELOW_THRESHOLD),
    ],
)
async def test_an_item_that_triage_stops_never_reaches_the_panel_or_the_judge(
    fields: dict[str, object], verdict: Verdict
) -> None:
    client = ScriptedLlm(triage=[triage(**fields)])

    decision = await pipeline(client).decide(item())

    assert decision.verdict is verdict and decision.triage is not None
    assert dict(client.calls) == {"triage": 1}  # no other call was made


async def test_a_triage_confidence_exactly_at_the_threshold_goes_on() -> None:
    client = ScriptedLlm(
        triage=[triage(confidence=60)], bull=[view()], bear=[view()], tape=[view()], judge=[judge()]
    )

    assert (await pipeline(client, threshold=60).decide(item())).verdict is Verdict.TRADE


async def test_with_the_panel_off_only_triage_and_the_judge_are_called() -> None:
    client = ScriptedLlm(triage=[TRIAGE_OK], judge=[judge()])

    decision = await pipeline(client, panel=False).decide(item())

    assert decision.verdict is Verdict.TRADE and decision.views == ()
    assert dict(client.calls) == {"triage": 1, "judge": 1}


async def test_an_invalid_reply_anywhere_is_no_trade_and_the_error_is_kept() -> None:
    client = ScriptedLlm(triage=[TRIAGE_OK], bull=["x", "x"], bear=[view()], tape=[view()])

    decision = await pipeline(client).decide(item())

    assert decision.verdict is Verdict.STAGE_ERROR and decision.plan is None
    assert decision.errors and decision.errors[0].startswith("invalid_reply")
    assert "judge" not in client.calls  # the judge is not asked when the panel is incomplete


async def test_a_failed_call_is_no_trade() -> None:
    client = ScriptedLlm(triage=[transport_error()])

    decision = await pipeline(client).decide(item())

    assert decision.verdict is Verdict.STAGE_ERROR and decision.errors[0].startswith("transport")


async def test_the_arbiter_can_veto_and_only_a_veto_from_a_judge_approved_candidate() -> None:
    def script(approve: bool) -> ScriptedLlm:
        return ScriptedLlm(
            triage=[TRIAGE_OK],
            bull=[view()],
            bear=[view()],
            tape=[view()],
            judge=[judge()],
            arbiter=[reply(approve=approve, confidence=50, reason="x")],
        )

    vetoed = await pipeline(script(False), arbiter=True).decide(item())
    approved = await pipeline(script(True), arbiter=True).decide(item())

    assert (
        vetoed.verdict is Verdict.ARBITER_VETO
        and vetoed.plan is None
        and vetoed.arbiter is not None
    )
    assert (
        approved.verdict is Verdict.TRADE
        and approved.arbiter is not None
        and approved.arbiter.approve
    )


async def test_the_arbiter_is_not_called_when_the_judge_or_panel_already_said_no() -> None:
    client = ScriptedLlm(
        triage=[TRIAGE_OK], bull=[view()], bear=[view()], tape=[view()], judge=[judge(trade=False)]
    )

    await pipeline(client, arbiter=True).decide(item())

    assert "arbiter" not in client.calls


def test_the_arbiter_switched_on_without_one_is_refused_and_so_is_a_bad_threshold() -> None:
    client = ScriptedLlm()
    with pytest.raises(ValueError, match="none was given"):
        DecisionPipeline(
            PipelineConfig(60, True, True),
            TriageStage(client, MODEL),
            (
                PanelistStage.bull(client, MODEL),
                PanelistStage.bear(client, MODEL),
                PanelistStage.tape(client, MODEL),
            ),
            JudgeStage(client, MODEL),
            None,
        )
    with pytest.raises(ValueError, match="0 to 100"):
        PipelineConfig(101)


async def test_a_swing_horizon_is_carried_through_from_the_judge_not_from_triage() -> None:
    client = ScriptedLlm(
        triage=[triage(horizon="intraday")],
        bull=[view("long", "cash_intraday")],
        bear=[view("long", "cash_intraday")],
        tape=[view("long", "cash_intraday")],
        judge=[judge(instrument="cash_intraday", hold_days=0)],
    )

    decision = await pipeline(client).decide(item())

    assert decision.triage is not None and decision.triage.horizon is Horizon.INTRADAY
    assert decision.plan is not None and decision.plan.instrument is Instrument.CASH_INTRADAY
