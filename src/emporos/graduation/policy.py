"""Which requirements each stage demands. Built at the composition root; adding a stage or a rule
is wiring, not an edit to a class. A stage with no entry cannot be promoted TO: that is how
PRODUCTION is refused in this release."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from emporos.domain.graduation import GraduationStage
from emporos.graduation.requirements import PromotionRequirement


class StagePolicy:
    def __init__(
        self, requirements: Mapping[GraduationStage, Sequence[PromotionRequirement]]
    ) -> None:
        for target, rules in requirements.items():
            if not rules:
                raise ValueError(
                    f"{target.value} lists no requirements: an empty list would promote on nothing"
                )
        self._requirements = {stage: tuple(rules) for stage, rules in requirements.items()}

    def promotable(self, target: GraduationStage) -> bool:
        return target in self._requirements

    def requirements_for(self, target: GraduationStage) -> tuple[PromotionRequirement, ...]:
        return self._requirements.get(target, ())


def standard_policy(
    paper: Sequence[PromotionRequirement], live: Sequence[PromotionRequirement]
) -> StagePolicy:
    """PAPER needs the research gates; LIVE_CONSERVATIVE needs those AND the live-only ones.

    The live list is `paper + live` by construction, so a research gate can never be skipped by
    going to live, and adding a paper requirement adds it to live too."""
    return StagePolicy(
        {
            GraduationStage.PAPER: tuple(paper),
            GraduationStage.LIVE_CONSERVATIVE: (*paper, *live),
        }
    )
