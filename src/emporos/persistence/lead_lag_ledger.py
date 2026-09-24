"""The lead-lag trial ledger in Mongo: append and read, and nothing else (EM-180).

Same shape as `emporos.persistence.feature_ledger.MongoFeatureTrialLedger`: `_id` is the trial
id, so recording the same trial twice is refused, never overwritten.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.experiments import TrialRole
from emporos.domain.lead_lag_trials import DuplicateLeadLagTrialError, LeadLagTrial
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import LeadLagTrialRecord
from emporos.persistence.repositories import LeadLagTrialRepository


class MongoLeadLagTrialLedger:
    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self._records = LeadLagTrialRepository(database)

    async def append(self, trial: LeadLagTrial) -> None:
        try:
            await self._records.insert(self._record(trial))
        except DuplicateRecordError as error:
            raise DuplicateLeadLagTrialError(trial.trial_id) from error

    async def count(self) -> int:
        """How many trials, without loading them (program-wide N, EM-191 F2)."""
        return await self._records.count()

    async def all(self) -> list[LeadLagTrial]:
        records = await self._records.find(
            {}, sort=[("recorded_at", ASCENDING), ("_id", ASCENDING)]
        )
        return [self._trial(record) for record in records]

    @staticmethod
    def _record(trial: LeadLagTrial) -> LeadLagTrialRecord:
        return LeadLagTrialRecord(
            _id=trial.trial_id,
            hypothesis_id=trial.hypothesis_id,
            predictor=trial.predictor,
            expression=trial.expression,
            subject=trial.subject,
            early_horizon_label=trial.early_horizon_label,
            target_horizon_label=trial.target_horizon_label,
            direction=trial.direction,
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
    def _trial(record: LeadLagTrialRecord) -> LeadLagTrial:
        return LeadLagTrial(
            trial_id=record.id,
            hypothesis_id=record.hypothesis_id,
            predictor=record.predictor,
            expression=record.expression,
            subject=record.subject,
            early_horizon_label=record.early_horizon_label,
            target_horizon_label=record.target_horizon_label,
            direction=record.direction,
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
