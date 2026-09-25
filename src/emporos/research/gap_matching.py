"""Explain a >=15% gap in the broker's daily bars by an exchange action (EM-221, A-F2).

What the data showed: at 87 of the 89 splits and bonuses the exchange lists for the D1 names, the
broker's daily series is CONTINUOUS across the ex-date. The broker adjusts its history
retroactively, and the local archive was fetched in chunks at different times, so a chunk fetched
before an action holds RAW prices and a chunk fetched after it holds ADJUSTED ones. The break sits
at a chunk boundary, on a date unrelated to the ex-date (CANBK's 1:5 split was on 2024-05-15; its
series steps by 0.1998 on 2022-05-10). Applying the exchange's ex-date to such a series would
double-adjust it and put a false jump at the ex-date.

So the exchange's records are used as EVIDENCE for a gap, not applied at their ex-dates. A gap at
day D (open over the previous close = g) is explained when the cumulative product R of the first k
actions with an ex-date after the previous session (k = 1..3, in time order) is within `tolerance`
of g: `|g / R - 1| <= tolerance`. The factor is then dated D, the first session on the adjusted
basis, with the exchange's exact ratio R. Candidate matches are taken best first (smallest error);
an action explains one gap only, so a name with more gaps than actions keeps the rest unexplained.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.research.adjustments import ActionKind, AdjustmentFactor
from emporos.research.corporate_actions import ReadAction

__all__ = ["DEFAULT_TOLERANCE", "GapMatcher", "MatchResult", "RawGap"]

DEFAULT_TOLERANCE = Decimal("0.05")
MAX_ACTIONS_PER_GAP = 3


@dataclass(frozen=True)
class RawGap:
    instrument_id: str
    day: date
    previous_day: date
    ratio: Decimal  # open / previous close on raw prices


@dataclass(frozen=True)
class MatchResult:
    factors: tuple[AdjustmentFactor, ...]
    unmatched: tuple[RawGap, ...]


class GapMatcher:
    def __init__(self, tolerance: Decimal = DEFAULT_TOLERANCE) -> None:
        if not Decimal(0) < tolerance < Decimal(1):
            raise ValueError("the tolerance is a fraction between 0 and 1")
        self._tolerance = tolerance

    def match(
        self,
        gaps: Sequence[RawGap],
        actions: Mapping[str, Sequence[ReadAction]],
        source: str,
    ) -> MatchResult:
        candidates: list[tuple[Decimal, int, int, RawGap, tuple[ReadAction, ...]]] = []
        for index, gap in enumerate(gaps):
            after = [a for a in actions.get(gap.instrument_id, ()) if a.ex_date > gap.previous_day]
            product = Decimal(1)
            for k, action in enumerate(after[:MAX_ACTIONS_PER_GAP], 1):
                product *= action.ratio
                error = abs(gap.ratio / product - 1)
                if error <= self._tolerance:
                    candidates.append((error, index, k, gap, tuple(after[:k])))
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        used_gaps: set[int] = set()
        used_actions: set[tuple[str, date, Decimal]] = set()
        factors: list[AdjustmentFactor] = []
        for _, index, _, gap, chosen in candidates:
            keys = {(gap.instrument_id, a.ex_date, a.ratio) for a in chosen}
            if index in used_gaps or keys & used_actions:
                continue
            used_gaps.add(index)
            used_actions |= keys
            factors.append(self._factor(gap, chosen, source))
        unmatched = tuple(g for i, g in enumerate(gaps) if i not in used_gaps)
        return MatchResult(
            tuple(sorted(factors, key=lambda f: (f.instrument_id, f.ex_date))), unmatched
        )

    @staticmethod
    def _factor(gap: RawGap, chosen: Sequence[ReadAction], source: str) -> AdjustmentFactor:
        ratio = Decimal(1)
        for action in chosen:
            ratio *= action.ratio
        kinds = {a.kind for a in chosen}
        kind = kinds.pop() if len(kinds) == 1 else ActionKind.OTHER
        subjects = "; ".join(f"{a.subject} (ex {a.ex_date})" for a in chosen)
        return AdjustmentFactor(
            gap.instrument_id,
            gap.day,
            ratio,
            kind,
            f"{source}; broker history changes basis on {gap.day}",
            subjects,
        )
