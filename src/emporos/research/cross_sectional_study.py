"""Turns one `CrossSectionalEngine.evaluate` result into recorded `CrossSectionalTrial`s (EM-179):
the seam between the pure evaluation engine and the append-only ledger, gated by `HoldoutGate` so
a TEST-role study can never silently run over anything but its pre-declared holdout. Fetching and
aligning the bars themselves is the composition root's job — this class only ever sees a universe
it is handed. Mirrors `emporos.research.study.FeatureStudy`'s shape exactly, one instrument-study
at a time versus one cross-sectional study over the whole universe at once.
"""

from __future__ import annotations

from datetime import date

from emporos.core.clock import Clock
from emporos.domain.experiments import TrialRole
from emporos.research.cross_sectional import CrossSectionalEngine, TailSegment
from emporos.research.cross_sectional_ledger import CrossSectionalTrial, CrossSectionalTrialLedger
from emporos.research.factors import AlignedUniverse
from emporos.research.hypotheses import HoldoutGate, HypothesisDeclaration


class CrossSectionalStudy:
    def __init__(
        self,
        engine: CrossSectionalEngine,
        ledger: CrossSectionalTrialLedger,
        clock: Clock,
        holdout_gate: HoldoutGate | None = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._clock = clock
        self._holdout_gate = holdout_gate or HoldoutGate()

    async def run(
        self,
        universe: AlignedUniverse,
        *,
        hypothesis: HypothesisDeclaration,
        role: TrialRole,
        dataset_version: str,
        study_first: date,
        study_last: date,
    ) -> list[CrossSectionalTrial]:
        self._holdout_gate.check(hypothesis, study_first, study_last, role=role)
        trials: list[CrossSectionalTrial] = []
        for segment in self._engine.evaluate(universe):
            trial = self._to_trial(hypothesis, segment, role, dataset_version)
            await self._ledger.append(trial)
            trials.append(trial)
        return trials

    def _to_trial(
        self,
        hypothesis: HypothesisDeclaration,
        segment: TailSegment,
        role: TrialRole,
        dataset_version: str,
    ) -> CrossSectionalTrial:
        report = segment.report
        trial_id = "|".join((
            hypothesis.hypothesis_id, role.value, segment.tail.value,
            segment.signal_horizon.label, segment.holding_horizon.label,
            segment.axis or "pooled", segment.label or "all",
        ))  # fmt: skip
        return CrossSectionalTrial(
            trial_id=trial_id,
            hypothesis_id=hypothesis.hypothesis_id,
            signal_horizon_label=segment.signal_horizon.label,
            holding_horizon_label=segment.holding_horizon.label,
            tail=segment.tail.value,
            role=role,
            dataset_version=dataset_version,
            cost_model=self._engine.cost_model_label,
            regime_axis=segment.axis,
            regime_label=segment.label,
            sample_size=report.sample_size,
            gross_expectancy=report.gross_expectancy,
            net_expectancy=report.net_expectancy,
            hit_rate=report.hit_rate,
            t_statistic=report.t_statistic,
            recorded_at=self._clock.now(),
        )
