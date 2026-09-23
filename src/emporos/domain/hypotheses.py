"""A pre-declared research hypothesis and the holdout period it commits to (EM-178), pure domain.

Declaring a hypothesis BEFORE a feature is evaluated, with a fixed holdout window, is what makes
"immutable holdout periods" a fact about the record rather than a promise about behaviour: once
declared, a hypothesis cannot be edited (there is no update path, only `declare`), so its holdout
dates cannot be moved after a result is seen. Enforcement that a study actually respects the
holdout (`HoldoutGate`, `HoldoutViolation`) lives in `emporos.research.hypotheses` — it needs
`core.errors.DefinitiveError`, which domain may never import (plan.md's "domain is pure").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class DuplicateHypothesisError(Exception):
    """A hypothesis with this id is already declared; declarations are never overwritten."""


@dataclass(frozen=True)
class HypothesisDeclaration:
    hypothesis_id: str
    feature_name: str
    feature_version: str
    study_first: date
    study_last: date
    holdout_first: date
    holdout_last: date
    declared_at: datetime

    def __post_init__(self) -> None:
        if not self.hypothesis_id:
            raise ValueError("a hypothesis needs an id")
        if self.study_last < self.study_first:
            raise ValueError("study_last cannot precede study_first")
        if self.holdout_last < self.holdout_first:
            raise ValueError("holdout_last cannot precede holdout_first")
        if not (self.study_first <= self.holdout_first and self.holdout_last <= self.study_last):
            raise ValueError("the holdout period must lie within the study period")
        if self.declared_at.tzinfo is None:
            raise ValueError("declared_at must be timezone-aware")

    def covers_holdout(self, first: date, last: date) -> bool:
        return first == self.holdout_first and last == self.holdout_last

    def touches_holdout(self, first: date, last: date) -> bool:
        return first <= self.holdout_last and self.holdout_first <= last
