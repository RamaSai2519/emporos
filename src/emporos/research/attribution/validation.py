"""The attribution validation pack (driver-atlas-plan §4.2, EM-245): everything the four bars need,
as code that runs on any set of results, with no model call.

1. PLACEBO: on quiet samples Call A must answer "unexplained" or give confidence < 50 on >= 70%;
2. STABILITY: re-run on a 20% sample with the candidate items shuffled and renamed: primary driver
   agreement >= 80%;
3. HEAD AUDIT: a stratified random sample of 60 attributions laid out against the raw items to be
   graded right / plausible / wrong; wrong <= 15%;
4. CROSS-CHECK: where a calendar fact exists, the agreement between it and the model's primary
   driver, reported per class."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from emporos.research.attribution.items import CauseItem
from emporos.research.attribution.results import Attribution
from emporos.research.attribution.taxonomy import Driver

__all__ = [
    "AttributedCase", "AuditSampler", "AuditVerdict", "Grade", "PLACEBO_PASS", "STABILITY_PASS",
    "WRONG_MAX", "audit_sheet", "audit_verdict", "crosscheck_by_class", "placebo_pass_rate",
    "shuffle_and_rename", "stability_agreement",
]  # fmt: skip

PLACEBO_PASS = 0.70
PLACEBO_CONFIDENCE = 50
STABILITY_PASS = 0.80
WRONG_MAX = 0.15
AUDIT_SIZE = 60


class Grade(StrEnum):
    RIGHT = "right"
    PLAUSIBLE = "plausible"
    WRONG = "wrong"


@dataclass(frozen=True)
class AttributedCase:
    case_id: str
    summary: str  # the move in words and numbers, as the model saw it
    items: Sequence[CauseItem]
    answer: Attribution
    placebo: bool = False
    calendar_driver: Driver | None = None  # the driver a calendar fact implies, when one exists

    @property
    def klass(self) -> str:
        return "placebo" if self.placebo else self.answer.primary.value


# --- 1 placebo ------------------------------------------------------------------------------
def placebo_pass_rate(cases: Sequence[AttributedCase]) -> tuple[float, bool]:
    """(the share of quiet samples answered "unexplained" or with confidence < 50, whether it
    reaches 70%). No placebo samples is a failure: the check cannot be skipped."""
    quiet = [c for c in cases if c.placebo]
    if not quiet:
        return 0.0, False
    ok = sum(1 for c in quiet if c.answer.unexplained or c.answer.confidence < PLACEBO_CONFIDENCE)
    return ok / len(quiet), ok / len(quiet) >= PLACEBO_PASS


# --- 2 stability ----------------------------------------------------------------------------
def shuffle_and_rename(case: AttributedCase, seed: int) -> AttributedCase:
    """The same case with its candidate items shuffled and given new ids (the cited id is mapped
    along), the input of the stability re-run. The item TEXT and times are untouched."""
    rng = random.Random(f"{seed}:{case.case_id}")
    items = list(case.items)
    rng.shuffle(items)
    names = {i.item_id: f"item-{n + 1}" for n, i in enumerate(items)}
    renamed = [replace(i, item_id=names[i.item_id]) for i in items]
    return replace(case, items=renamed)


def stability_agreement(
    first: Mapping[str, Driver], second: Mapping[str, Driver]
) -> tuple[float, bool]:
    """Primary-driver agreement over the cases both runs answered, and whether it reaches 80%."""
    common = sorted(first.keys() & second.keys())
    if not common:
        return 0.0, False
    same = sum(1 for k in common if first[k] == second[k])
    return same / len(common), same / len(common) >= STABILITY_PASS


# --- 3 the head's audit -----------------------------------------------------------------------
class AuditSampler:
    """A stratified random sample by the model's class (placebo is its own stratum), seeded, so the
    head reads the same 60 every time and the model cannot know which."""

    def __init__(self, seed: int, size: int = AUDIT_SIZE) -> None:
        self._seed, self._size = seed, size

    def sample(self, cases: Sequence[AttributedCase]) -> list[AttributedCase]:
        strata: dict[str, list[AttributedCase]] = defaultdict(list)
        for case in sorted(cases, key=lambda c: c.case_id):
            strata[case.klass].append(case)
        quota = self._quotas({k: len(v) for k, v in strata.items()})
        rng = random.Random(self._seed)
        picked: list[AttributedCase] = []
        for klass in sorted(strata):
            picked += rng.sample(strata[klass], quota[klass])
        return sorted(picked, key=lambda c: c.case_id)

    def _quotas(self, sizes: Mapping[str, int]) -> dict[str, int]:
        """One per class while there is room, the rest in proportion (largest remainder)."""
        total = sum(sizes.values())
        if total <= self._size:
            return dict(sizes)
        classes = sorted(sizes)
        base = (
            {k: min(1, sizes[k]) for k in classes}
            if len(classes) <= self._size
            else {k: 0 for k in classes}
        )
        spare = self._size - sum(base.values())
        share = {k: spare * sizes[k] / total for k in classes}
        quota = {k: min(sizes[k], base[k] + int(share[k])) for k in classes}
        order = sorted(classes, key=lambda k: (-(share[k] - int(share[k])), k))
        while sum(quota.values()) < self._size:
            grew = False
            for k in order:
                if quota[k] < sizes[k] and sum(quota.values()) < self._size:
                    quota[k] += 1
                    grew = True
            if not grew:
                break
        return quota


def audit_sheet(cases: Sequence[AttributedCase]) -> str:
    """The reading sheet: per case the move, every candidate item with its text, the model's answer
    and its cited item, and a line to grade right / plausible / wrong."""
    lines = ["# Attribution audit: grade each RIGHT, PLAUSIBLE or WRONG", ""]
    for n, case in enumerate(cases, 1):
        a = case.answer
        cited = a.cited_item or "none"
        lines += [
            f"## {n}. {case.case_id}  [{case.klass}]",
            f"Move: {case.summary}",
            "Items:",
            *[f"- {i.item_id} ({i.item_class.value}): {i.text[:300]}" for i in case.items],
            *([] if case.items else ["- (no candidate items)"]),
            f"Model: primary {a.primary.value}, secondary "
            f"{a.secondary.value if a.secondary else 'none'}, confidence {a.confidence}, "
            f"cites {cited}. Reason: {a.reason}",
            "Grade: ____",
            "",
        ]
    return "\n".join(lines)


@dataclass(frozen=True)
class AuditVerdict:
    graded: int
    wrong_share: float
    passed: bool
    by_class: dict[str, tuple[int, int]]  # class -> (graded, wrong)


def audit_verdict(grades: Mapping[str, Grade], cases: Sequence[AttributedCase]) -> AuditVerdict:
    klass = {c.case_id: c.klass for c in cases}
    by_class: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case_id, grade in grades.items():
        by_class[klass[case_id]][0] += 1
        by_class[klass[case_id]][1] += grade is Grade.WRONG
    graded = len(grades)
    wrong = sum(1 for g in grades.values() if g is Grade.WRONG)
    share = wrong / graded if graded else 1.0
    return AuditVerdict(
        graded,
        share,
        graded > 0 and share <= WRONG_MAX,
        {k: (v[0], v[1]) for k, v in sorted(by_class.items())},
    )


# --- 4 cross-check ----------------------------------------------------------------------------
def crosscheck_by_class(cases: Sequence[AttributedCase]) -> dict[str, tuple[int, float]]:
    """Per calendar-implied driver: (cases with such a fact, the share where the model's PRIMARY
    driver agrees). Reported, no bar."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in cases:
        if case.calendar_driver is None:
            continue
        counts[case.calendar_driver.value][0] += 1
        counts[case.calendar_driver.value][1] += case.answer.primary is case.calendar_driver
    return {k: (v[0], v[1] / v[0]) for k, v in sorted(counts.items())}
