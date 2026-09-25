"""EM-240: strict JSON reading, each stage's validation, and the retry-once rule."""

from __future__ import annotations

import json

import pytest

from emporos.eventtrader.events import MarketContext
from emporos.eventtrader.llm.reply import InvalidReply, Reply
from emporos.eventtrader.stages.models import (
    Horizon,
    Instrument,
    Posture,
    Side,
    TriageDirection,
)
from emporos.eventtrader.stages.prompts import (
    ARBITER_V1,
    BEAR_V1,
    BULL_V1,
    JUDGE_V1,
    POSTURE_V2,
    TAPE_V1,
    TRIAGE_V1,
)
from emporos.eventtrader.stages.stages import (
    ArbiterInput,
    ArbiterStage,
    EventInput,
    JudgeInput,
    JudgeStage,
    PanelistStage,
    PostureInput,
    PostureStage,
    TriageStage,
)
from tests.unit.eventtrader.fakes import (
    NOON,
    TRIAGE_OK,
    ScriptedLlm,
    item,
    judge,
    reply,
    transport_error,
    view,
)

MODEL = "openai/gpt-4o-mini"


class TestReply:
    def test_a_fenced_object_is_read_and_a_bare_one_too(self) -> None:
        assert Reply('```json\n{"a": true}\n```').boolean("a") is True
        assert Reply(' {"a": true} ').boolean("a") is True

    @pytest.mark.parametrize("text", ["", "no", "[1]", '{"a": 1', "null"])
    def test_anything_that_is_not_a_json_object_is_invalid(self, text: str) -> None:
        with pytest.raises(InvalidReply):
            Reply(text)

    def test_nothing_is_coerced_or_defaulted(self) -> None:
        r = Reply('{"b": "true", "n": "5", "flag": true, "c": "x", "big": 200}')

        with pytest.raises(InvalidReply, match="true or false"):
            r.boolean("b")
        with pytest.raises(InvalidReply, match="a number"):
            r.number("n", 0, 10)
        with pytest.raises(InvalidReply, match="a number"):
            r.number("flag", 0, 10)  # a bool is not a number
        with pytest.raises(InvalidReply, match="between"):
            r.number("big", 0, 100)
        with pytest.raises(InvalidReply, match="one of"):
            r.choice("c", ["y", "z"])
        with pytest.raises(InvalidReply, match="missing missing"):
            r.text("missing")

    def test_reasons_are_truncated_not_trusted(self) -> None:
        assert len(Reply(json.dumps({"reason": "x" * 5000})).text("reason")) == 500


class TestTriage:
    async def test_a_valid_reply_becomes_a_frozen_value(self) -> None:
        stage = TriageStage(ScriptedLlm(triage=[TRIAGE_OK]), MODEL)

        outcome = await stage.run(item(), NOON)

        t = outcome.value
        assert t is not None and outcome.attempts == 1 and outcome.error is None
        assert (t.material, t.direction, t.horizon, t.priced_in, t.confidence) == (
            True,
            TriageDirection.UP,
            Horizon.SWING,
            False,
            80,
        )
        assert t.expected_move_pct == 3.0

    async def test_an_out_of_range_field_is_invalid_not_clipped(self) -> None:
        bad = reply(
            material=True,
            direction="up",
            expected_move_pct=90,
            horizon="swing",
            priced_in=False,
            confidence=80,
            reason="x",
        )
        client = ScriptedLlm(triage=[bad, bad])

        outcome = await TriageStage(client, MODEL).run(item(), NOON)

        assert outcome.value is None and "expected_move_pct" in (outcome.error or "")


class TestRetryOnce:
    async def test_an_invalid_reply_is_asked_again_once_with_a_reminder(self) -> None:
        client = ScriptedLlm(triage=["not json", TRIAGE_OK])

        outcome = await TriageStage(client, MODEL).run(item(), NOON)

        assert outcome.value is not None and outcome.attempts == 2
        first, second = client.requests
        assert second.user.startswith(first.user) and "not valid" in second.user
        assert first.request_hash != second.request_hash  # a recorded bad answer is not replayed

    async def test_two_invalid_replies_end_as_no_value_and_never_a_third_call(self) -> None:
        client = ScriptedLlm(triage=["nope", "still nope", TRIAGE_OK])

        outcome = await TriageStage(client, MODEL).run(item(), NOON)

        assert outcome.value is None and outcome.attempts == 2 and client.calls["triage"] == 2
        assert (outcome.error or "").startswith("invalid_reply")

    async def test_a_transport_failure_is_not_retried_by_the_stage(self) -> None:
        client = ScriptedLlm(triage=[transport_error(), TRIAGE_OK])

        outcome = await TriageStage(client, MODEL).run(item(), NOON)

        assert outcome.value is None and client.calls["triage"] == 1
        assert (outcome.error or "").startswith("transport")


class TestRequestsCarryNoCalendarDate:
    async def test_the_user_message_has_the_item_the_time_of_day_and_the_numbers_only(self) -> None:
        client = ScriptedLlm(triage=[TRIAGE_OK])

        await TriageStage(client, MODEL).run(item(text="Q3 results " + "y" * 9000), NOON)

        sent = client.requests[0]
        body = json.loads(sent.user)
        assert (
            body["item"]["company"] == "RELIANCE"
            and body["item"]["minutes_since_it_appeared"] == 12
        )
        assert (
            body["time_of_day_ist"] == "12:00"
            and body["market_numbers"]["move_since_close_pct"] == 1.23
        )
        assert len(body["item"]["text"]) == 3000
        assert "2024" not in sent.user and "03-04" not in sent.user and "as_of" not in sent.user
        assert sent.as_of == NOON  # for the date guard and the journal, never in the prompt

    async def test_the_same_inputs_give_the_same_question(self) -> None:
        a, b = ScriptedLlm(triage=[TRIAGE_OK]), ScriptedLlm(triage=[TRIAGE_OK])

        await TriageStage(a, MODEL).run(item(), NOON)
        await TriageStage(b, MODEL).run(item(), NOON)

        assert a.requests[0].request_hash == b.requests[0].request_hash

    def test_every_prompt_is_versioned_and_hashed_and_none_names_a_calendar_date(self) -> None:
        prompts = [TRIAGE_V1, BULL_V1, BEAR_V1, TAPE_V1, JUDGE_V1, ARBITER_V1, POSTURE_V2]

        assert len({p.content_hash for p in prompts}) == len(prompts)
        assert all(
            p.version.rsplit("-v", 1)[1].isdigit() and len(p.content_hash) == 64 for p in prompts
        )
        assert not [p.version for p in prompts if "20" in p.system_text.replace("0-20", "")]


class TestPanelist:
    async def test_each_persona_reads_its_own_prompt_and_returns_a_view(self) -> None:
        client = ScriptedLlm(
            bull=[view("long")],
            bear=[view("none", "none", 10)],
            tape=[view("short", "cash_intraday")],
        )
        views = [
            (await PanelistStage.bull(client, MODEL).run(item(), NOON)).value,
            (await PanelistStage.bear(client, MODEL).run(item(), NOON)).value,
            (await PanelistStage.tape(client, MODEL).run(item(), NOON)).value,
        ]

        assert [v.persona for v in views if v] == ["bull", "bear", "tape"]
        assert (
            views[0] and views[0].side is Side.LONG and views[0].instrument is Instrument.CASH_SWING
        )
        assert views[1] and views[1].side is None and views[1].instrument is None
        assert len({r.system for r in client.requests}) == 3

    async def test_a_side_without_an_instrument_is_invalid(self) -> None:
        bad = reply(side="long", conviction=50, instrument="none", reason="x")
        outcome = await PanelistStage.bull(ScriptedLlm(bull=[bad, bad]), MODEL).run(item(), NOON)

        assert outcome.value is None and "both be none" in (outcome.error or "")


class TestJudgeAndArbiter:
    async def views(self) -> tuple[ScriptedLlm, list]:  # type: ignore[type-arg]
        client = ScriptedLlm(bull=[view()], bear=[view("short")], tape=[view()])
        out = [
            (await s.run(item(), NOON)).value
            for s in (
                PanelistStage.bull(client, MODEL),
                PanelistStage.bear(client, MODEL),
                PanelistStage.tape(client, MODEL),
            )
        ]
        return client, [v for v in out if v]

    async def test_a_trade_needs_an_instrument_a_side_and_a_positive_stop_and_target(self) -> None:
        _, views = await self.views()
        for bad in (
            judge(instrument="none"),
            judge(side="none"),
            judge(stop_pct=0),
            judge(target_pct=0),
            judge(instrument="cash_intraday", hold_days=3),
            judge(instrument="cash_swing", hold_days=0),
        ):
            outcome = await JudgeStage(ScriptedLlm(judge=[bad, bad]), MODEL).run(
                JudgeInput(item(), views), NOON
            )
            assert outcome.value is None, bad

    async def test_a_no_trade_is_valid_without_the_trade_fields(self) -> None:
        _, views = await self.views()

        outcome = await JudgeStage(ScriptedLlm(judge=[judge(trade=False)]), MODEL).run(
            JudgeInput(item(), views), NOON
        )

        assert (
            outcome.value is not None
            and outcome.value.trade is False
            and outcome.value.instrument is None
        )

    async def test_the_judge_sees_all_three_views_and_the_arbiter_sees_the_proposal(self) -> None:
        _, views = await self.views()
        client = ScriptedLlm(
            judge=[judge()], arbiter=[reply(approve=False, confidence=40, reason="thin")]
        )
        judged = await JudgeStage(client, MODEL).run(JudgeInput(item(), views), NOON)
        assert judged.value is not None
        arbiter = await ArbiterStage(client, "gpt-4o-2024-08-06").run(
            ArbiterInput(JudgeInput(item(), views), judged.value), NOON
        )

        assert [p["role"] for p in json.loads(client.requests[0].user)["panel"]] == [
            "bull",
            "bear",
            "tape",
        ]
        proposal = json.loads(client.requests[1].user)["proposed_trade"]
        assert proposal["instrument"] == "cash_swing" and proposal["stop_pct"] == 3.0
        assert arbiter.value is not None and arbiter.value.approve is False

    async def test_with_the_panel_off_the_judge_sees_an_empty_panel(self) -> None:
        client = ScriptedLlm(judge=[judge()])

        await JudgeStage(client, MODEL).run(JudgeInput(item(), ()), NOON)

        assert json.loads(client.requests[0].user)["panel"] == []


class TestPosture:
    async def test_the_posture_is_one_of_three_and_carries_no_date(self) -> None:
        client = ScriptedLlm(posture=[reply(posture="hold", reason="hostile")])
        stage = PostureStage(client, MODEL)

        outcome = await stage.run(
            PostureInput(["Sensex falls 2%"], {"nifty_gap_pct": -1.1, "vix": 19.5}, NOON), NOON
        )

        assert outcome.value is not None and outcome.value.posture is Posture.HOLD
        assert "2024" not in client.requests[0].user

    async def test_an_unknown_posture_is_invalid(self) -> None:
        bad = reply(posture="yolo", reason="x")

        outcome = await PostureStage(ScriptedLlm(posture=[bad, bad]), MODEL).run(
            PostureInput([], {}, NOON), NOON
        )

        assert outcome.value is None


class TestOpenInterestReachesThePromptOnce:
    async def test_each_open_interest_key_appears_exactly_once_in_the_request(self) -> None:
        keys = ("oi_asof", "fut_open_interest", "fut_oi_change_pct", "put_call_oi_ratio")
        lines = {"last_price": 100.0, "oi_asof": "2024-03-01", "fut_open_interest": 1.5e6,
                 "fut_oi_change_pct": -2.5, "put_call_oi_ratio": 0.8}  # fmt: skip
        client = ScriptedLlm(triage=[TRIAGE_OK])
        given = EventInput(item().event, MarketContext(lines), NOON)

        await TriageStage(client, MODEL).run(given, NOON)

        sent = client.requests[0].user
        assert [sent.count(f'"{k}"') for k in keys] == [1, 1, 1, 1]
