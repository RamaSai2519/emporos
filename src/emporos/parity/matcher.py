"""Lines paper's signals up with the backtest's, one to one.

Two signals are candidates when they share (instrument, side, kind) and their times are within a
tolerance; among candidates the nearest in time wins, then the earlier journal sequence. Matching
is greedy in time order and strictly one-to-one. Whatever finds no partner on either side is a
first-class result, never dropped: a signal only one side produced is the finding.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol

from emporos.parity.models import SignalMatch, SignalPoint


class MatchPolicy(Protocol):
    def distance(self, paper: SignalPoint, backtest: SignalPoint) -> timedelta | None:
        """How far apart the two are, or None when they cannot be the same signal."""
        ...


class WithinTolerance:
    def __init__(self, tolerance: timedelta = timedelta(0)) -> None:
        if tolerance < timedelta(0):
            raise ValueError("a tolerance cannot be negative")
        self._tolerance = tolerance

    def distance(self, paper: SignalPoint, backtest: SignalPoint) -> timedelta | None:
        if paper.key != backtest.key:
            return None
        gap = abs(paper.ts - backtest.ts)
        return gap if gap <= self._tolerance else None


class SignalMatcher:
    def __init__(self, policy: MatchPolicy | None = None) -> None:
        self._policy: MatchPolicy = policy or WithinTolerance()

    def match(
        self, paper: Sequence[SignalPoint], backtest: Sequence[SignalPoint]
    ) -> tuple[SignalMatch, ...]:
        unmatched = sorted(backtest, key=lambda p: (p.ts, p.sequence))
        results: list[SignalMatch] = []
        for signal in sorted(paper, key=lambda p: (p.ts, p.sequence)):
            partner = self._nearest(signal, unmatched)
            if partner is not None:
                unmatched.remove(partner)
            results.append(SignalMatch(signal, partner))
        results.extend(SignalMatch(None, left_over) for left_over in unmatched)
        return tuple(results)

    def _nearest(
        self, signal: SignalPoint, candidates: Sequence[SignalPoint]
    ) -> SignalPoint | None:
        best: tuple[timedelta, int, SignalPoint] | None = None
        for candidate in candidates:
            gap = self._policy.distance(signal, candidate)
            if gap is None:
                continue
            rank = (gap, candidate.sequence, candidate)
            if best is None or rank[:2] < best[:2]:
                best = rank
        return None if best is None else best[2]
