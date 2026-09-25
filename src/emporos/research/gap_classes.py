"""Real move or data artifact? The rule for a >= 15% open gap (EM-221, ruled 2026-09-25).

A gap the exchange's actions explain is EXPLAINED (adjusted out with the exchange's own ratio).
Every other gap is either a REAL price move, which a strategy must trade through with its real
P&L, or an ARTIFACT of the broker's history (a fetch-chunk boundary), which must not appear as a
return. The rule, in order:

1. EXPLAINED: the adjustment ledger explains it.
2. REAL when the market moved with it: the index's close-to-close move on that session has the same
   sign as the gap and at least half its size (2020-03-23: NIFTY -13%, names -16..-20%).
3. ARTIFACT when it is split-shaped (a ratio a split or bonus makes) or larger than 25% in size, and
   the index did not move with it.
4. REAL otherwise: an ordinary large idiosyncratic move (a circuit-limit print, results, a
   regulator's action, the Adani day). It is traded through; nothing is quarantined and nothing is
   zeroed, because a deleted loss flatters every long-only rule.

The thresholds are fixed here, before any screen, and each classified gap carries its reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from emporos.research.discontinuities import DiscontinuityStatus, Finding

__all__ = [
    "ARTIFACT_SIZE",
    "COMPARABLE_SHARE",
    "GapClass",
    "GapClassifier",
    "GapVerdict",
    "IndexMoves",
]

COMPARABLE_SHARE = Decimal("0.5")  # the index moved at least this share of the gap, same sign
ARTIFACT_SIZE = Decimal("0.25")  # a larger gap with no index move is a data artifact


class GapClass(StrEnum):
    EXPLAINED = "explained"
    REAL = "real"
    ARTIFACT = "artifact"


class IndexMoves(Protocol):
    def close_to_close(self, day: date) -> Decimal | None:
        """The index's close over its previous close on session `day`, minus 1; None if unknown."""
        ...


@dataclass(frozen=True)
class GapVerdict:
    finding: Finding
    gap_class: GapClass
    reason: str
    index_move: Decimal | None  # the index's close-to-close move that session


class GapClassifier:
    def __init__(
        self,
        index: IndexMoves,
        comparable: Decimal = COMPARABLE_SHARE,
        artifact_size: Decimal = ARTIFACT_SIZE,
    ) -> None:
        self._index = index
        self._comparable = comparable
        self._artifact_size = artifact_size

    def classify(self, finding: Finding) -> GapVerdict:
        move = self._index.close_to_close(finding.day)
        gap = finding.raw_ratio - 1
        if finding.status is DiscontinuityStatus.EXPLAINED:
            return GapVerdict(
                finding, GapClass.EXPLAINED, "an exchange split/bonus explains it", move
            )
        if move is not None and move * gap > 0 and abs(move) >= self._comparable * abs(gap):
            reason = f"the index moved {move:+.1%} the same session (gap {gap:+.1%})"
            return GapVerdict(finding, GapClass.REAL, reason, move)
        if finding.shape is not None:
            reason = f"split-shaped ({finding.shape}) and the index did not move with it"
            return GapVerdict(finding, GapClass.ARTIFACT, reason, move)
        if abs(gap) > self._artifact_size:
            reason = (
                f"{gap:+.1%} is larger than {self._artifact_size:.0%} and the index did not move"
            )
            return GapVerdict(finding, GapClass.ARTIFACT, reason, move)
        return GapVerdict(finding, GapClass.REAL, f"a large single-name move ({gap:+.1%})", move)
