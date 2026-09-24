"""The feature-trial ledger in Mongo: append and read, and nothing else (EM-178).

Same shape as `emporos.persistence.trial_ledger.MongoTrialLedger`: `_id` is the trial id, so
recording the same trial twice is refused, never overwritten.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.experiments import TrialRole
from emporos.domain.feature_trials import DuplicateFeatureTrialError, FeatureTrial
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import FeatureTrialRecord
from emporos.persistence.repositories import FeatureTrialRepository


class MongoFeatureTrialLedger:
    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self._records = FeatureTrialRepository(database)

    async def append(self, trial: FeatureTrial) -> None:
        try:
            await self._records.insert(self._record(trial))
        except DuplicateRecordError as error:
            raise DuplicateFeatureTrialError(trial.trial_id) from error

    async def count(self) -> int:
        """How many trials, without loading them (program-wide N, EM-191 F2)."""
        return await self._records.count()

    async def all(self) -> list[FeatureTrial]:
        records = await self._records.find(
            {}, sort=[("recorded_at", ASCENDING), ("_id", ASCENDING)]
        )
        return [self._trial(record) for record in records]

    @staticmethod
    def _record(trial: FeatureTrial) -> FeatureTrialRecord:
        return FeatureTrialRecord(
            _id=trial.trial_id,
            hypothesis_id=trial.hypothesis_id,
            feature_name=trial.feature_name,
            feature_version=trial.feature_version,
            horizon_label=trial.horizon_label,
            role=trial.role.value,
            dataset_version=trial.dataset_version,
            cost_model=trial.cost_model,
            regime_axis=trial.regime_axis,
            regime_label=trial.regime_label,
            sample_size=trial.sample_size,
            conditional_expectancy=trial.conditional_expectancy,
            cost_adjusted_expectancy=trial.cost_adjusted_expectancy,
            hit_rate=trial.hit_rate,
            rank_ic=trial.rank_ic,
            decile_spread=trial.decile_spread,
            t_statistic=trial.t_statistic,
            recorded_at=trial.recorded_at,
            note=trial.note,
        )

    @staticmethod
    def _trial(record: FeatureTrialRecord) -> FeatureTrial:
        return FeatureTrial(
            trial_id=record.id,
            hypothesis_id=record.hypothesis_id,
            feature_name=record.feature_name,
            feature_version=record.feature_version,
            horizon_label=record.horizon_label,
            role=TrialRole(record.role),
            dataset_version=record.dataset_version,
            cost_model=record.cost_model,
            regime_axis=record.regime_axis,
            regime_label=record.regime_label,
            sample_size=record.sample_size,
            conditional_expectancy=record.conditional_expectancy,
            cost_adjusted_expectancy=record.cost_adjusted_expectancy,
            hit_rate=record.hit_rate,
            rank_ic=record.rank_ic,
            decile_spread=record.decile_spread,
            t_statistic=record.t_statistic,
            recorded_at=record.recorded_at,
            note=record.note,
        )
