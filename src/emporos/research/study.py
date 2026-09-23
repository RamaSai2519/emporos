"""Turns one `AlphaDiscoveryEngine.evaluate` result into recorded `FeatureTrial`s (EM-178): the
seam between the pure evaluation engine and the append-only ledger, gated by `HoldoutGate` so a
TEST-role study can never silently run over anything but its pre-declared holdout. Fetching the
bars themselves (`CachingCandleReader`/`FileCandleReader`) is the composition root's job — this
class only ever sees bars it is handed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from emporos.core.clock import Clock
from emporos.domain.candles import Candle
from emporos.domain.experiments import TrialRole
from emporos.research.engine import AlphaDiscoveryEngine, Segment
from emporos.research.features import Feature, FeatureDefinition
from emporos.research.hypotheses import HoldoutGate, HypothesisDeclaration
from emporos.research.ledger import FeatureTrial, FeatureTrialLedger


class FeatureStudy:
    def __init__(
        self,
        engine: AlphaDiscoveryEngine,
        ledger: FeatureTrialLedger,
        clock: Clock,
        holdout_gate: HoldoutGate | None = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._clock = clock
        self._holdout_gate = holdout_gate or HoldoutGate()

    async def run(
        self,
        definition: FeatureDefinition,
        feature: Feature,
        bars_by_instrument: Mapping[str, Sequence[Candle]],
        *,
        hypothesis: HypothesisDeclaration,
        role: TrialRole,
        dataset_version: str,
        study_first: date,
        study_last: date,
    ) -> list[FeatureTrial]:
        self._holdout_gate.check(hypothesis, study_first, study_last, role=role)
        trials: list[FeatureTrial] = []
        for instrument_id, bars in bars_by_instrument.items():
            for segment in self._engine.evaluate(feature, bars):
                trial = self._to_trial(
                    hypothesis, definition, instrument_id, segment, role, dataset_version
                )
                await self._ledger.append(trial)
                trials.append(trial)
        return trials

    def _to_trial(
        self,
        hypothesis: HypothesisDeclaration,
        definition: FeatureDefinition,
        instrument_id: str,
        segment: Segment,
        role: TrialRole,
        dataset_version: str,
    ) -> FeatureTrial:
        report = segment.report
        trial_id = "|".join((
            hypothesis.hypothesis_id, role.value, definition.content_hash[-16:],
            instrument_id, segment.horizon.label, segment.axis or "pooled", segment.label or "all",
        ))  # fmt: skip
        return FeatureTrial(
            trial_id=trial_id,
            hypothesis_id=hypothesis.hypothesis_id,
            feature_name=definition.name,
            feature_version=definition.version,
            horizon_label=segment.horizon.label,
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
