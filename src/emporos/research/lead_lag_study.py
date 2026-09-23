"""Turns one `LeadLagEngine.evaluate` result into recorded `LeadLagTrial`s (EM-180): the seam
between the pure evaluation engine and the append-only ledger, gated by `HoldoutGate` exactly as
`FeatureStudy`/`CrossSectionalStudy` are. One call evaluates one (predictor, expression, subject)
combination — "NIFTY proxy predicting stock X", say; a study of several predictors, or several
targets, is the composition root calling `run` once per combination, not a parameter here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from emporos.core.clock import Clock
from emporos.domain.candles import Candle
from emporos.domain.experiments import TrialRole
from emporos.domain.money import Money
from emporos.research.hypotheses import HoldoutGate, HypothesisDeclaration
from emporos.research.lead_lag import LeadLagEngine, LeadLagSegment
from emporos.research.lead_lag_ledger import LeadLagTrial, LeadLagTrialLedger


class LeadLagStudy:
    def __init__(
        self,
        engine: LeadLagEngine,
        ledger: LeadLagTrialLedger,
        clock: Clock,
        holdout_gate: HoldoutGate | None = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._clock = clock
        self._holdout_gate = holdout_gate or HoldoutGate()

    async def run(
        self,
        predictor_by_day: Mapping[date, Sequence[Decimal]],
        target_by_day: Mapping[date, Sequence[Decimal]],
        regime_bars_by_day: Mapping[date, Sequence[Candle]],
        price_by_day: Mapping[date, Money] | None,
        *,
        predictor: str,
        expression: str,
        subject: str,
        hypothesis: HypothesisDeclaration,
        role: TrialRole,
        dataset_version: str,
        study_first: date,
        study_last: date,
    ) -> list[LeadLagTrial]:
        self._holdout_gate.check(hypothesis, study_first, study_last, role=role)
        trials: list[LeadLagTrial] = []
        segments = self._engine.evaluate(
            predictor_by_day, target_by_day, regime_bars_by_day, price_by_day
        )
        for segment in segments:
            trial = self._to_trial(
                hypothesis, predictor, expression, subject, segment, role, dataset_version
            )
            await self._ledger.append(trial)
            trials.append(trial)
        return trials

    def _to_trial(
        self,
        hypothesis: HypothesisDeclaration,
        predictor: str,
        expression: str,
        subject: str,
        segment: LeadLagSegment,
        role: TrialRole,
        dataset_version: str,
    ) -> LeadLagTrial:
        report = segment.report
        trial_id = "|".join((
            hypothesis.hypothesis_id, role.value, predictor, expression, subject,
            segment.early_horizon.label, segment.target_horizon_label, segment.direction,
            segment.axis or "pooled", segment.label or "all",
        ))  # fmt: skip
        return LeadLagTrial(
            trial_id=trial_id,
            hypothesis_id=hypothesis.hypothesis_id,
            predictor=predictor,
            expression=expression,
            subject=subject,
            early_horizon_label=segment.early_horizon.label,
            target_horizon_label=segment.target_horizon_label,
            direction=segment.direction,
            role=role,
            dataset_version=dataset_version,
            cost_model=self._engine.cost_model_label,
            regime_axis=segment.axis,
            regime_label=segment.label,
            sample_size=report.sample_size,
            conditional_expectancy=report.conditional_expectancy,
            cost_adjusted_expectancy=report.cost_adjusted_expectancy,
            hit_rate=report.hit_rate,
            rank_ic=report.rank_ic,
            decile_spread=report.decile_spread,
            t_statistic=report.t_statistic,
            recorded_at=self._clock.now(),
        )
