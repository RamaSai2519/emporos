"""The cross-sectional trial ledger in Mongo: append and read, and nothing else (EM-179).

Same shape as `emporos.persistence.feature_ledger.MongoFeatureTrialLedger`: `_id` is the trial
id, so recording the same trial twice is refused, never overwritten.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.cross_sectional_trials import (
    CrossSectionalTrial,
    DuplicateCrossSectionalTrialError,
)
from emporos.domain.experiments import TrialRole
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import CrossSectionalTrialRecord
from emporos.persistence.repositories import CrossSectionalTrialRepository


class MongoCrossSectionalTrialLedger:
    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self._records = CrossSectionalTrialRepository(database)

    async def append(self, trial: CrossSectionalTrial) -> None:
        try:
            await self._records.insert(self._record(trial))
        except DuplicateRecordError as error:
            raise DuplicateCrossSectionalTrialError(trial.trial_id) from error

    async def count(self) -> int:
        """How many trials, without loading them (program-wide N, EM-191 F2)."""
        return await self._records.count()

    async def all(self) -> list[CrossSectionalTrial]:
        records = await self._records.find(
            {}, sort=[("recorded_at", ASCENDING), ("_id", ASCENDING)]
        )
        return [self._trial(record) for record in records]

    @staticmethod
    def _record(trial: CrossSectionalTrial) -> CrossSectionalTrialRecord:
        return CrossSectionalTrialRecord(
            _id=trial.trial_id,
            hypothesis_id=trial.hypothesis_id,
            signal_horizon_label=trial.signal_horizon_label,
            holding_horizon_label=trial.holding_horizon_label,
            tail=trial.tail,
            role=trial.role.value,
            dataset_version=trial.dataset_version,
            cost_model=trial.cost_model,
            regime_axis=trial.regime_axis,
            regime_label=trial.regime_label,
            sample_size=trial.sample_size,
            gross_expectancy=trial.gross_expectancy,
            net_expectancy=trial.net_expectancy,
            hit_rate=trial.hit_rate,
            t_statistic=trial.t_statistic,
            recorded_at=trial.recorded_at,
            note=trial.note,
        )

    @staticmethod
    def _trial(record: CrossSectionalTrialRecord) -> CrossSectionalTrial:
        return CrossSectionalTrial(
            trial_id=record.id,
            hypothesis_id=record.hypothesis_id,
            signal_horizon_label=record.signal_horizon_label,
            holding_horizon_label=record.holding_horizon_label,
            tail=record.tail,
            role=TrialRole(record.role),
            dataset_version=record.dataset_version,
            cost_model=record.cost_model,
            regime_axis=record.regime_axis,
            regime_label=record.regime_label,
            sample_size=record.sample_size,
            gross_expectancy=record.gross_expectancy,
            net_expectancy=record.net_expectancy,
            hit_rate=record.hit_rate,
            t_statistic=record.t_statistic,
            recorded_at=record.recorded_at,
            note=record.note,
        )
