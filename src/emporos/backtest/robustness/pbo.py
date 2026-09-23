"""Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation (Bailey,
Borwein, de Prado, Zhu, 2014): of every candidate parameter set tried, did the one that looked
best in-sample tend to be just an average (or worse) performer out-of-sample?

Classic CSCV splits a single return series into S equal time BLOCKS and enumerates every way to
split those blocks into two equal halves. Here, the blocks already exist as a natural, causal unit:
one walk-forward TRAINING window (`emporos.backtest.tuning.TrainingScore`, produced once per
candidate per window by `WalkForwardRunner`, already a purely in-sample number since the selector
that reads it is typed to see nothing else). No new return series, persistence, or re-splitting of
raw returns is needed — a walk-forward window IS a CSCV block, and this module only ever consumes
`Decimal` scores already computed elsewhere, over the SAME candidates, in the SAME window order.

For each of the C(S, S/2) ways to choose half the windows as the "in-sample" half (the rest are
"out-of-sample"): the candidate with the best mean IS score is found, and its OOS relative rank
`omega = rank / (N + 1)` (rank 1 = worst OOS, N = best OOS, among N candidates) is converted to a
logit `ln(omega / (1 - omega))`. PBO is the fraction of splits where that logit is at or below
zero — the in-sample winner performed at or below the out-of-sample MEDIAN, the signature of a
result that only looked good because it was chosen after the fact. PBO near 0 is reassuring; PBO
near 1 means the winning candidate was consistently a below-median performer once you looked
somewhere else, exactly the pattern overfitting to one training window produces.

Deterministic and exhaustive — the exact set of C(S, S/2) combinations is walked in a fixed order
(candidates broken alphabetically on ties), so two runs over the same scores always agree: this is
what "reproducible" means here, unlike `monte_carlo.py`'s seeded resampling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from itertools import combinations
from math import comb

from emporos.backtest.metrics.decimal_math import CONTEXT, ONE, ZERO, DecimalMath

MIN_CANDIDATES = 2
MIN_BLOCKS = 4  # at least 2 windows per half
MAX_COMBINATIONS = 200_000  # C(20, 10); a hard stop against a runaway exhaustive search


@dataclass(frozen=True)
class PBOReport:
    candidate_count: int
    block_count: int  # windows actually used (one dropped if the count was odd)
    combination_count: int
    probability_of_overfitting: Decimal | None
    mean_logit: Decimal | None  # negative on average = the search tends to overfit
    reason: str | None

    @property
    def computed(self) -> bool:
        return self.probability_of_overfitting is not None


class CSCV:
    def evaluate(self, scores: Mapping[str, Sequence[Decimal]]) -> PBOReport:
        candidate_count = len(scores)
        if candidate_count < MIN_CANDIDATES:
            return self._missing(
                candidate_count, 0, f"{candidate_count} candidate(s) scored; need {MIN_CANDIDATES}"
            )
        lengths = {len(series) for series in scores.values()}
        if len(lengths) != 1:
            reason = "candidates were not scored over the same windows"
            return self._missing(candidate_count, 0, reason)
        block_count = lengths.pop()
        if block_count < MIN_BLOCKS:
            return self._missing(
                candidate_count, block_count, f"{block_count} window(s) scored; need {MIN_BLOCKS}"
            )
        if block_count % 2:
            block_count -= 1  # keep the split exactly symmetric
        half = block_count // 2
        combination_count = comb(block_count, half)
        if combination_count > MAX_COMBINATIONS:
            return self._missing(
                candidate_count, block_count,
                f"{combination_count} combinations over {block_count} windows exceeds the "
                f"{MAX_COMBINATIONS} exhaustive-search limit",
            )  # fmt: skip
        names = sorted(scores)
        with localcontext(CONTEXT):
            logits = self._logits(names, scores, block_count, half)
            pbo = DecimalMath.divide(
                Decimal(sum(1 for logit in logits if logit <= ZERO)), Decimal(len(logits))
            )
            mean_logit = DecimalMath.mean(logits)
        return PBOReport(candidate_count, block_count, len(logits), pbo, mean_logit, None)

    @staticmethod
    def _logits(
        names: Sequence[str],
        scores: Mapping[str, Sequence[Decimal]],
        block_count: int,
        half: int,
    ) -> list[Decimal]:
        logits: list[Decimal] = []
        for in_sample in combinations(range(block_count), half):
            out_of_sample = tuple(i for i in range(block_count) if i not in in_sample)
            is_mean = {n: DecimalMath.mean([scores[n][i] for i in in_sample]) for n in names}
            oos_mean = {n: DecimalMath.mean([scores[n][i] for i in out_of_sample]) for n in names}
            best = max(names, key=lambda n: is_mean[n])
            rank = sum(1 for n in names if oos_mean[n] <= oos_mean[best])  # 1 = worst, N = best
            omega = DecimalMath.divide(Decimal(rank), Decimal(len(names) + 1))
            logits.append(DecimalMath.divide(omega, ONE - omega).ln())
        return logits

    @staticmethod
    def _missing(candidate_count: int, block_count: int, reason: str) -> PBOReport:
        return PBOReport(candidate_count, block_count, 0, None, None, reason)
