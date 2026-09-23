"""Pre-declared hypotheses and immutable holdout periods (EM-178).

A hypothesis is declared — the feature, its version, and the holdout window it must not be tuned
against — BEFORE any TEST-role trial may be recorded against it. `HoldoutGate` enforces this as a
structural, raising invariant (mirroring EM-177's `ResearchIntegrityGate`): a TEST trial whose
dates are not EXACTLY the pre-declared holdout is refused, and a TRAIN/STANDALONE trial that
touches any day inside a declared holdout is refused too — the search that produced a result must
never have seen the days it will later be judged on. `HypothesisDeclaration` itself is pure domain
(`emporos.domain.hypotheses`); this module holds the orchestration-layer enforcement, which needs
`core.errors.DefinitiveError` and so cannot live in domain.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from emporos.core.errors import DefinitiveError
from emporos.domain.experiments import TrialRole
from emporos.domain.hypotheses import DuplicateHypothesisError, HypothesisDeclaration

__all__ = [
    "DuplicateHypothesisError", "HoldoutGate", "HoldoutViolation", "HypothesisDeclaration",
    "HypothesisRegistry", "InMemoryHypothesisRegistry",
]  # fmt: skip


class HoldoutViolation(DefinitiveError):
    """A trial's dates conflict with a pre-declared hypothesis's holdout — refused, not warned."""


class HypothesisRegistry(Protocol):
    async def declare(self, declaration: HypothesisDeclaration) -> None:
        """Raises `DuplicateHypothesisError` when the id is already declared."""
        ...

    async def get(self, hypothesis_id: str) -> HypothesisDeclaration | None: ...


class InMemoryHypothesisRegistry:
    def __init__(self) -> None:
        self._declarations: dict[str, HypothesisDeclaration] = {}

    async def declare(self, declaration: HypothesisDeclaration) -> None:
        if declaration.hypothesis_id in self._declarations:
            raise DuplicateHypothesisError(declaration.hypothesis_id)
        self._declarations[declaration.hypothesis_id] = declaration

    async def get(self, hypothesis_id: str) -> HypothesisDeclaration | None:
        return self._declarations.get(hypothesis_id)


class HoldoutGate:
    """Checked before recording a trial: whether the dates it covers are safe for its role."""

    def check(
        self, declaration: HypothesisDeclaration, first: date, last: date, *, role: TrialRole
    ) -> None:
        if role is TrialRole.TEST:
            if not declaration.covers_holdout(first, last):
                raise HoldoutViolation(
                    f"a TEST trial for {declaration.hypothesis_id} must cover exactly the "
                    f"pre-declared holdout {declaration.holdout_first}..{declaration.holdout_last}"
                    f", not {first}..{last}"
                )
        elif declaration.touches_holdout(first, last):
            raise HoldoutViolation(
                f"a {role.value.upper()} trial for {declaration.hypothesis_id} may not touch its "
                f"pre-declared holdout {declaration.holdout_first}..{declaration.holdout_last}"
            )
