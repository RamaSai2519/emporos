"""Graduation events and acknowledgements: what is a valid fact, and when a stage goes stale."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from emporos.domain.graduation import (
    EvidenceKind,
    EvidenceRef,
    GraduationEvent,
    GraduationStage,
    LiveAcknowledgement,
    TransitionKind,
    acknowledgement_phrase,
    effective_stage,
    short_hash,
)

AT = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
S = GraduationStage
HASH = "abcdef0123456789"
EVIDENCE = (EvidenceRef(EvidenceKind.VERDICT, "verdict-1"),)


def event(
    origin: GraduationStage = S.RESEARCH,
    target: GraduationStage = S.PAPER,
    kind: TransitionKind = TransitionKind.PROMOTE,
    **changes: object,
) -> GraduationEvent:
    base = GraduationEvent(
        "orb_v1", HASH, 1, origin, target, kind, EVIDENCE, "rama", "gates passed", AT
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_the_stages_are_ordered_and_retired_is_off_the_road() -> None:
    assert [s.rank for s in (S.RESEARCH, S.PAPER, S.LIVE_CONSERVATIVE, S.PRODUCTION)] == [
        0,
        1,
        2,
        3,
    ]
    assert S.RETIRED.rank < 0


class TestPromotion:
    def test_one_stage_up_with_evidence_is_valid(self) -> None:
        assert event().to_stage is S.PAPER
        assert event(S.PAPER, S.LIVE_CONSERVATIVE).to_stage is S.LIVE_CONSERVATIVE

    @pytest.mark.parametrize(
        ("origin", "target"),
        [(S.RESEARCH, S.LIVE_CONSERVATIVE), (S.PAPER, S.PAPER), (S.PAPER, S.RESEARCH),
         (S.RETIRED, S.RESEARCH), (S.PRODUCTION, S.RETIRED)],
    )  # fmt: skip
    def test_anything_but_the_next_stage_is_refused(
        self, origin: GraduationStage, target: GraduationStage
    ) -> None:
        with pytest.raises(ValueError):
            event(origin, target)

    def test_a_promotion_without_evidence_is_refused(self) -> None:
        with pytest.raises(ValueError, match="evidence"):
            event(evidence=())


class TestDemotionAndRetirement:
    def test_a_demotion_may_fall_any_distance_and_needs_no_evidence(self) -> None:
        demote = event(S.LIVE_CONSERVATIVE, S.RESEARCH, TransitionKind.DEMOTE, evidence=())
        assert demote.to_stage is S.RESEARCH

    @pytest.mark.parametrize(
        ("origin", "target"),
        [(S.PAPER, S.PAPER), (S.RESEARCH, S.PAPER), (S.PAPER, S.RETIRED), (S.RETIRED, S.PAPER)],
    )
    def test_a_demotion_must_go_down_and_never_touches_retired(
        self, origin: GraduationStage, target: GraduationStage
    ) -> None:
        with pytest.raises(ValueError):
            event(origin, target, TransitionKind.DEMOTE)

    def test_a_retirement_goes_to_retired_from_anywhere_else(self) -> None:
        assert event(S.PAPER, S.RETIRED, TransitionKind.RETIRE).to_stage is S.RETIRED
        with pytest.raises(ValueError, match="retirement"):
            event(S.PAPER, S.PAPER, TransitionKind.RETIRE)
        with pytest.raises(ValueError, match="retirement"):
            event(S.RETIRED, S.RETIRED, TransitionKind.RETIRE)


class TestEventValidation:
    @pytest.mark.parametrize("field", ["strategy", "behaviour_hash", "actor", "reason"])
    def test_a_blank_text_field_is_refused(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            event(**{field: "  "})

    def test_the_sequence_starts_at_one_and_the_time_is_utc(self) -> None:
        with pytest.raises(ValueError, match="sequence"):
            event(seq=0)
        with pytest.raises(ValueError, match="UTC"):
            event(at=datetime(2026, 9, 24, 4, 0))
        with pytest.raises(ValueError, match="UTC"):
            event(at=AT.astimezone(timezone(timedelta(hours=5))))

    def test_evidence_must_point_at_something(self) -> None:
        with pytest.raises(ValueError, match="point"):
            EvidenceRef(EvidenceKind.EXPERIMENT, " ")

    def test_an_event_is_immutable(self) -> None:
        with pytest.raises(AttributeError):
            event().seq = 2  # type: ignore[misc]


class TestEffectiveStage:
    def test_no_event_is_research(self) -> None:
        assert effective_stage(None, HASH) is S.RESEARCH

    def test_the_last_event_for_this_configuration_applies(self) -> None:
        assert effective_stage(event(), HASH) is S.PAPER

    def test_an_edited_configuration_goes_back_to_research(self) -> None:
        assert effective_stage(event(S.PAPER, S.LIVE_CONSERVATIVE), "0" * 16) is S.RESEARCH

    def test_a_retirement_holds_whatever_the_configuration(self) -> None:
        retired = event(S.PAPER, S.RETIRED, TransitionKind.RETIRE)
        assert effective_stage(retired, HASH) is S.RETIRED
        assert effective_stage(retired, "0" * 16) is S.RETIRED


class TestAcknowledgement:
    def ack(self, **changes: object) -> LiveAcknowledgement:
        base = LiveAcknowledgement(
            "orb_v1", HASH, "rama", acknowledgement_phrase("orb_v1", HASH), "live_conservative", AT
        )
        return replace(base, **changes)  # type: ignore[arg-type]

    def test_the_phrase_is_the_strategy_and_the_first_eight_of_the_hash(self) -> None:
        assert acknowledgement_phrase("orb_v1", HASH) == "orb_v1@abcdef01 LIVE"
        assert self.ack().operator == "rama"

    @pytest.mark.parametrize("typed", ["", "orb_v1@abcdef01 live", "orb_v1@abcdef01 LIVE ", "yes"])
    def test_a_phrase_that_is_not_exact_is_refused(self, typed: str) -> None:
        with pytest.raises(ValueError, match="phrase"):
            self.ack(typed_phrase=typed)

    def test_a_phrase_for_another_configuration_is_refused(self) -> None:
        with pytest.raises(ValueError, match="phrase"):
            self.ack(behaviour_hash="1" * 16)

    @pytest.mark.parametrize("field", ["operator", "risk_tier"])
    def test_a_blank_field_and_a_naive_time_are_refused(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            self.ack(**{field: ""})
        with pytest.raises(ValueError, match="UTC"):
            self.ack(at=datetime(2026, 9, 24))


def test_the_short_hash_skips_the_label_every_hash_shares() -> None:
    """`sha256:6f3a...` -> `6f3a...`, or the phrase would name a config by one character."""
    assert short_hash("sha256:0123456789abcdef") == "01234567"
    assert short_hash("abcdef0123456789") == "abcdef01"
    assert acknowledgement_phrase("orb_v1", "sha256:0123456789abcdef") == "orb_v1@01234567 LIVE"
