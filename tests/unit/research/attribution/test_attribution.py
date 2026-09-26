"""EM-245 scaffolding: the guard, the prompts, the three stages on a fake client, the validation
pack. No model is ever called."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from emporos.eventtrader.llm.client import LlmReply, LlmRequest
from emporos.research.attribution.coverage import NO_HEADLINES, Coverage
from emporos.research.attribution.guard import (
    HindsightLeak,
    assert_public,
    candidates,
    post_onset,
    public_by,
)
from emporos.research.attribution.items import CauseItem, ItemClass
from emporos.research.attribution.prompts import (
    ATTRIBUTION_V1,
    CROSSING_CAUSE_V1,
    DIRECTION_V1,
)
from emporos.research.attribution.results import Attribution, Outlook, Way
from emporos.research.attribution.stages import (
    AttributionStage,
    CauseOnly,
    CrossingCase,
    CrossingCauseStage,
    DirectionStage,
    MoveCase,
    check_citation,
)
from emporos.research.attribution.taxonomy import Driver
from emporos.research.attribution.validation import (
    AttributedCase,
    AuditSampler,
    Grade,
    audit_sheet,
    audit_verdict,
    crosscheck_by_class,
    placebo_pass_rate,
    shuffle_and_rename,
    stability_agreement,
)

T0 = datetime(2024, 3, 4, 5, 0, tzinfo=UTC)  # 10:30 IST
MODEL = "gpt-4o-mini-2024-07-18"


def item(
    item_id: str,
    minutes: int,
    text: str = "Q3 results announced",
    cls: ItemClass = ItemClass.FILING,
) -> CauseItem:
    return CauseItem(item_id, cls, text, T0 + timedelta(minutes=minutes), "RELIANCE")


class FakeClient:
    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmReply:
        self.requests.append(request)
        return LlmReply(self._answers.pop(0), 100, 20, request.model)


def reply(**fields: object) -> str:
    return json.dumps(fields)


class TestGuard:
    def test_an_item_public_after_the_cutoff_is_a_leak_not_a_quiet_filter(self) -> None:
        with pytest.raises(HindsightLeak):
            assert_public([item("a", -30), item("b", 1)], T0)
        assert_public([item("a", -30), item("b", 0)], T0)  # public AT the cutoff is public

    def test_call_a_candidates_are_the_24_hours_before_and_30_minutes_after_the_onset(self) -> None:
        items = [item("old", -25 * 60), item("in", -23 * 60), item("late", 31), item("edge", 30)]

        assert [i.item_id for i in candidates(items, T0)] == ["in", "edge"]

    def test_post_onset_is_flagged_never_hidden(self) -> None:
        assert post_onset(item("x", 10), T0) and not post_onset(item("y", -10), T0)

    def test_public_by_sorts_oldest_first_and_drops_later_items(self) -> None:
        shown = public_by([item("b", -5), item("a", -50), item("c", 5)], T0)

        assert [i.item_id for i in shown] == ["a", "b"]

    def test_a_naive_availability_time_is_refused(self) -> None:
        with pytest.raises(ValueError):
            CauseItem("x", ItemClass.FILING, "t", datetime(2024, 3, 4, 10, 0))


class TestPrompts:
    def test_they_are_versioned_hashed_and_carry_the_taxonomy_and_the_unexplained_answer(
        self,
    ) -> None:
        for prompt in (ATTRIBUTION_V1, CROSSING_CAUSE_V1):
            assert "U unexplained" in prompt.system_text and len(prompt.content_hash) == 64
        assert ATTRIBUTION_V1.version != CROSSING_CAUSE_V1.version != DIRECTION_V1.version
        assert "quiet session" in ATTRIBUTION_V1.system_text
        assert "NOT see how prices moved" in DIRECTION_V1.system_text

    def test_no_prompt_names_a_calendar_date(self) -> None:
        for prompt in (ATTRIBUTION_V1, CROSSING_CAUSE_V1, DIRECTION_V1):
            assert "2024" not in prompt.system_text and "2023" not in prompt.system_text


class TestStages:
    async def test_call_a_renders_candidates_and_the_coverage_gap_and_parses_the_answer(
        self,
    ) -> None:
        client = FakeClient(
            reply(primary="C1", secondary="none", confidence=80, cited_item="a", reason="results")
        )
        case = MoveCase(
            "RELIANCE", "stock", 1, T0, {"residual_pct": 2.4}, [item("a", -20), item("z", -30 * 60)]
        )
        outcome = await AttributionStage(client, MODEL).run(case, T0)

        sent = json.loads(client.requests[0].user)
        assert [i["id"] for i in sent["items"]] == ["a"]  # 'z' is outside the 24-hour window
        assert NO_HEADLINES in sent["not_available"]
        assert "2024" not in client.requests[0].user  # no calendar date
        assert outcome.value == Attribution(Driver.C1, None, 80, "a", "results")

    async def test_call_c_refuses_an_item_from_after_the_crossing(self) -> None:
        client = FakeClient(
            reply(
                driver="C1",
                outlook="continue",
                confidence=70,
                horizon_minutes=60,
                cited_item="none",
                reason="r",
            )
        )
        stage = CrossingCauseStage(client, MODEL)
        leaky = CrossingCase(
            "RELIANCE", 1, T0, {"path_pct": 1.0}, {"nifty_pct": 0.1}, [item("late", 5)]
        )

        with pytest.raises(HindsightLeak):
            await stage.run(leaky, T0)
        assert client.requests == []  # nothing reached the model

    async def test_call_c_sends_only_public_items_and_parses_the_call(self) -> None:
        client = FakeClient(
            reply(
                driver="F4",
                outlook="continue",
                confidence=72,
                horizon_minutes=90,
                cited_item="a",
                reason="deal",
            )
        )
        case = CrossingCase(
            "RELIANCE", -1, T0, {"path_pct": -1.6}, {"nifty_pct": 0.1}, [item("a", -12)]
        )
        outcome = await CrossingCauseStage(client, MODEL).run(case, T0)

        sent = json.loads(client.requests[0].user)
        assert (
            sent["crossing"]["direction"] == "down"
            and sent["items"][0]["minutes_since_public"] == 12
        )
        assert outcome.value is not None
        assert (outcome.value.driver, outcome.value.outlook, outcome.value.horizon_minutes) == (
            Driver.F4, Outlook.CONTINUE, 90,
        )  # fmt: skip

    async def test_call_b_is_blind_to_the_move(self) -> None:
        with pytest.raises(ValueError, match="blind"):
            CauseOnly(item("a", 0), {"stock_return_pct": 2.0})
        client = FakeClient(reply(way="up", horizon_minutes=120, confidence=65, reason="beat"))
        outcome = await DirectionStage(client, MODEL).run(
            CauseOnly(item("a", 0), {"nifty_level": 22000.0}), T0
        )

        sent = json.loads(client.requests[0].user)
        assert set(sent) == {"item", "numbers_then", "not_available"}
        assert outcome.value is not None and outcome.value.way is Way.UP

    async def test_an_invalid_reply_is_retried_once_then_gives_no_value(self) -> None:
        client = FakeClient(
            "not json",
            reply(primary="Z9", secondary="none", confidence=1, cited_item="none", reason="r"),
        )
        outcome = await AttributionStage(client, MODEL).run(
            MoveCase("X", "stock", 1, T0, {}, []), T0
        )

        assert outcome.value is None and outcome.attempts == 2 and len(client.requests) == 2

    def test_coverage_says_what_is_missing_and_can_be_changed(self) -> None:
        assert NO_HEADLINES in Coverage().lines()
        assert Coverage(frozenset()).lines() == []

    def test_citations_are_checked_by_code(self) -> None:
        items = [item("a", -10), item("b", 10)]

        assert check_citation(None, items, T0) == "none"
        assert check_citation("ghost", items, T0) == "unknown"
        assert check_citation("a", items, T0) == "before_onset"
        assert check_citation("b", items, T0) == "post_onset"


def case(
    n: int,
    driver: Driver,
    confidence: int = 80,
    placebo: bool = False,
    calendar: Driver | None = None,
) -> AttributedCase:
    return AttributedCase(
        f"c{n:03d}", f"move {n}", [item(f"i{n}", -5), item(f"j{n}", -9)],
        Attribution(driver, None, confidence, f"i{n}", "because"), placebo, calendar,
    )  # fmt: skip


class TestValidation:
    def test_placebo_needs_70_percent_unexplained_or_low_confidence(self) -> None:
        good = (
            [case(i, Driver.U) for i in range(6)]
            + [case(6, Driver.C1, 40)]
            + [case(i, Driver.C1, 90) for i in (7, 8, 9)]
        )
        rate, passed = placebo_pass_rate([replace_placebo(c) for c in good])

        assert rate == 0.7 and passed
        bad, failed = placebo_pass_rate([replace_placebo(case(i, Driver.C1, 90)) for i in range(4)])
        assert bad == 0.0 and not failed
        assert placebo_pass_rate([case(1, Driver.C1)]) == (0.0, False)  # no placebos: cannot pass

    def test_stability_needs_80_percent_agreement(self) -> None:
        a = {f"c{i}": Driver.C1 for i in range(10)}
        b = {**a, "c0": Driver.G1, "c1": Driver.G2}

        assert stability_agreement(a, b) == (0.8, True)
        assert stability_agreement(a, {**b, "c2": Driver.U}) == (0.7, False)
        assert stability_agreement(a, {}) == (0.0, False)

    def test_shuffle_and_rename_keeps_text_and_times_but_changes_ids_and_order(self) -> None:
        original = case(1, Driver.C1)
        renamed = shuffle_and_rename(original, 7)

        assert sorted(i.text for i in renamed.items) == sorted(i.text for i in original.items)
        assert {i.available_at for i in renamed.items} == {i.available_at for i in original.items}
        assert all(i.item_id.startswith("item-") for i in renamed.items)
        assert shuffle_and_rename(original, 7) == renamed  # seeded

    def test_the_audit_sample_is_stratified_seeded_and_the_requested_size(self) -> None:
        cases = [case(i, Driver.C1) for i in range(80)] + [
            case(100 + i, Driver.G1) for i in range(30)
        ]
        cases += [case(200 + i, Driver.P2) for i in range(4)] + [
            replace_placebo(case(300 + i, Driver.U)) for i in range(20)
        ]
        picked = AuditSampler(seed=5).sample(cases)

        assert len(picked) == 60 and len({c.case_id for c in picked}) == 60
        assert {c.klass for c in picked} == {"C1", "G1", "P2", "placebo"}  # every stratum is read
        assert picked == AuditSampler(seed=5).sample(cases)
        assert picked != AuditSampler(seed=6).sample(cases)

    def test_a_small_pool_is_taken_whole(self) -> None:
        cases = [case(i, Driver.C1) for i in range(5)]

        assert len(AuditSampler(seed=1).sample(cases)) == 5

    def test_the_sheet_lays_out_the_items_the_answer_and_a_grade_line(self) -> None:
        sheet = audit_sheet([case(1, Driver.C1), case(2, Driver.U)])

        assert "## 1. c001  [C1]" in sheet and "Grade: ____" in sheet
        assert "i1 (filing): Q3 results announced" in sheet and "cites i1" in sheet

    def test_wrong_over_15_percent_fails_the_audit(self) -> None:
        cases = [case(i, Driver.C1) for i in range(20)]
        grades = {c.case_id: Grade.RIGHT for c in cases}
        for c in cases[:3]:
            grades[c.case_id] = Grade.WRONG

        verdict = audit_verdict(grades, cases)
        assert verdict.wrong_share == 0.15 and verdict.passed and verdict.by_class["C1"] == (20, 3)
        grades[cases[3].case_id] = Grade.WRONG
        assert not audit_verdict(grades, cases).passed
        assert not audit_verdict({}, cases).passed

    def test_the_cross_check_reports_agreement_per_calendar_class(self) -> None:
        cases = [case(1, Driver.C1, calendar=Driver.C1), case(2, Driver.U, calendar=Driver.C1),
                 case(3, Driver.G2, calendar=Driver.G2), case(4, Driver.C1)]  # fmt: skip

        assert crosscheck_by_class(cases) == {"C1": (2, 0.5), "G2": (1, 1.0)}


def replace_placebo(c: AttributedCase) -> AttributedCase:
    from dataclasses import replace

    return replace(c, placebo=True)
