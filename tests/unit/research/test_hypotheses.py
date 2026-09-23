"""EM-178: `HypothesisDeclaration`, `InMemoryHypothesisRegistry` and `HoldoutGate`."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from emporos.domain.experiments import TrialRole
from emporos.domain.hypotheses import DuplicateHypothesisError, HypothesisDeclaration
from emporos.research.hypotheses import HoldoutGate, HoldoutViolation, InMemoryHypothesisRegistry

DECLARED = datetime(2026, 3, 1, tzinfo=UTC)
STUDY_FIRST, STUDY_LAST = date(2025, 1, 1), date(2026, 1, 1)
HOLDOUT_FIRST, HOLDOUT_LAST = date(2025, 10, 1), date(2026, 1, 1)


def declaration(hypothesis_id: str = "h1") -> HypothesisDeclaration:
    return HypothesisDeclaration(
        hypothesis_id, "momentum", "v1", STUDY_FIRST, STUDY_LAST, HOLDOUT_FIRST, HOLDOUT_LAST,
        DECLARED,
    )  # fmt: skip


def test_the_holdout_must_lie_within_the_study_period() -> None:
    with pytest.raises(ValueError, match="holdout period"):
        HypothesisDeclaration(
            "h1", "momentum", "v1", date(2025, 6, 1), date(2025, 12, 1),
            date(2025, 1, 1), date(2025, 3, 1), DECLARED,
        )  # fmt: skip


def test_declared_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        HypothesisDeclaration(
            "h1", "momentum", "v1", STUDY_FIRST, STUDY_LAST, HOLDOUT_FIRST, HOLDOUT_LAST,
            datetime(2026, 3, 1),
        )  # fmt: skip


async def test_the_registry_refuses_to_declare_the_same_id_twice() -> None:
    registry = InMemoryHypothesisRegistry()
    await registry.declare(declaration())

    with pytest.raises(DuplicateHypothesisError):
        await registry.declare(declaration())


async def test_the_registry_returns_none_for_an_unknown_id() -> None:
    registry = InMemoryHypothesisRegistry()

    assert await registry.get("missing") is None


def test_a_test_trial_must_cover_exactly_the_declared_holdout() -> None:
    gate = HoldoutGate()

    gate.check(declaration(), HOLDOUT_FIRST, HOLDOUT_LAST, role=TrialRole.TEST)  # does not raise

    with pytest.raises(HoldoutViolation):
        gate.check(declaration(), HOLDOUT_FIRST, date(2025, 12, 1), role=TrialRole.TEST)


def test_a_train_trial_may_not_touch_the_declared_holdout() -> None:
    gate = HoldoutGate()

    gate.check(declaration(), STUDY_FIRST, date(2025, 9, 1), role=TrialRole.TRAIN)  # does not raise

    with pytest.raises(HoldoutViolation):
        gate.check(declaration(), STUDY_FIRST, date(2025, 11, 1), role=TrialRole.TRAIN)


def test_a_standalone_trial_is_held_to_the_same_rule_as_train() -> None:
    gate = HoldoutGate()

    with pytest.raises(HoldoutViolation):
        gate.check(declaration(), STUDY_FIRST, date(2025, 11, 1), role=TrialRole.STANDALONE)
