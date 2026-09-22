from __future__ import annotations

import pytest

from emporos.domain.experiments import Verdict
from emporos.strategies.graduation import GraduationGate, GraduationStageError
from emporos.strategies.metadata import DeploymentStatus, ValidationStatus


@pytest.fixture
def gate() -> GraduationGate:
    return GraduationGate()


def test_a_validated_verdict_advances_to_the_target_stage(gate: GraduationGate) -> None:
    outcome = gate.advance_validation(
        ValidationStatus.RESEARCH, ValidationStatus.BACKTESTED, Verdict.VALIDATED
    )

    assert outcome.status is ValidationStatus.BACKTESTED
    assert outcome.promoted is True


def test_a_rejected_verdict_resets_to_research(gate: GraduationGate) -> None:
    outcome = gate.advance_validation(
        ValidationStatus.WALK_FORWARD_VALIDATED,
        ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
        Verdict.REJECTED,
    )

    assert outcome.status is ValidationStatus.RESEARCH
    assert outcome.promoted is False


def test_an_inconclusive_verdict_holds_at_the_current_stage(gate: GraduationGate) -> None:
    outcome = gate.advance_validation(
        ValidationStatus.BACKTESTED, ValidationStatus.WALK_FORWARD_VALIDATED, Verdict.INCONCLUSIVE
    )

    assert outcome.status is ValidationStatus.BACKTESTED
    assert outcome.promoted is False


def test_validation_cannot_skip_a_stage(gate: GraduationGate) -> None:
    with pytest.raises(GraduationStageError, match="one stage at a time"):
        gate.advance_validation(
            ValidationStatus.RESEARCH,
            ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
            Verdict.VALIDATED,
        )


def test_validation_cannot_go_backwards_via_advance(gate: GraduationGate) -> None:
    with pytest.raises(GraduationStageError):
        gate.advance_validation(
            ValidationStatus.BACKTESTED, ValidationStatus.RESEARCH, Verdict.VALIDATED
        )


def test_deployment_requires_out_of_sample_validation_first(gate: GraduationGate) -> None:
    outcome = gate.advance_deployment(
        ValidationStatus.WALK_FORWARD_VALIDATED,
        DeploymentStatus.CANDIDATE,
        DeploymentStatus.PAPER,
    )

    assert outcome.promoted is False
    assert outcome.status is DeploymentStatus.CANDIDATE
    assert "out_of_sample_validated" in outcome.reason


def test_deployment_advances_one_stage_when_fully_validated(gate: GraduationGate) -> None:
    outcome = gate.advance_deployment(
        ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
        DeploymentStatus.CANDIDATE,
        DeploymentStatus.PAPER,
    )

    assert outcome.promoted is True
    assert outcome.status is DeploymentStatus.PAPER


def test_deployment_can_reach_live_conservative_then_production(gate: GraduationGate) -> None:
    to_conservative = gate.advance_deployment(
        ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
        DeploymentStatus.PAPER,
        DeploymentStatus.LIVE_CONSERVATIVE,
    )
    to_production = gate.advance_deployment(
        ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
        DeploymentStatus.LIVE_CONSERVATIVE,
        DeploymentStatus.PRODUCTION,
    )

    assert to_conservative.status is DeploymentStatus.LIVE_CONSERVATIVE
    assert to_production.status is DeploymentStatus.PRODUCTION


def test_a_retired_strategy_cannot_be_promoted(gate: GraduationGate) -> None:
    with pytest.raises(GraduationStageError, match="retired"):
        gate.advance_deployment(
            ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
            DeploymentStatus.RETIRED,
            DeploymentStatus.PAPER,
        )


def test_deployment_cannot_skip_a_stage(gate: GraduationGate) -> None:
    with pytest.raises(GraduationStageError, match="one stage at a time"):
        gate.advance_deployment(
            ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
            DeploymentStatus.CANDIDATE,
            DeploymentStatus.LIVE_CONSERVATIVE,
        )


def test_an_unrecognized_target_stage_is_refused(gate: GraduationGate) -> None:
    with pytest.raises(GraduationStageError):
        gate.advance_deployment(
            ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
            DeploymentStatus.CANDIDATE,
            DeploymentStatus.RETIRED,  # RETIRED is terminal, not reached by normal advancement
        )
