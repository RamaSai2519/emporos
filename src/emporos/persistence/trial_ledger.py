"""The trial ledger in Mongo: append and read, and nothing else.

It holds a `Repository` rather than being one, so `replace` and `delete` are not reachable through
it: an experiment that was tried cannot be edited or forgotten after its result is seen. The unique
`_id` is the trial id, so recording the same trial twice is refused, never overwritten.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.experiments import DuplicateTrialError, Trial, TrialRole, Verdict
from emporos.domain.money import Money
from emporos.persistence.collections import Collection
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import TrialRecord
from emporos.persistence.repository import Repository


class MongoTrialLedger:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        collection: str = Collection.TRIAL_LEDGER,
    ) -> None:
        self._records = Repository(database, collection, TrialRecord)

    async def append(self, trial: Trial) -> None:
        try:
            await self._records.insert(self._record(trial))
        except DuplicateRecordError as error:
            raise DuplicateTrialError(trial.trial_id) from error

    async def all(self) -> list[Trial]:
        records = await self._records.find(
            {}, sort=[("recorded_at", ASCENDING), ("_id", ASCENDING)]
        )
        return [self._trial(record) for record in records]

    @staticmethod
    def _record(trial: Trial) -> TrialRecord:
        return TrialRecord(
            _id=trial.trial_id,
            experiment=trial.experiment,
            strategy=trial.strategy,
            candidate=trial.candidate,
            role=trial.role.value,
            dataset_version=trial.dataset_version,
            config_hash=trial.config_hash,
            cost_model=trial.cost_model,
            recorded_at=trial.recorded_at,
            run_id=trial.run_id,
            trade_count=trial.trade_count,
            net_pnl=None if trial.net_pnl is None else Money(trial.net_pnl),
            daily_sharpe=trial.daily_sharpe,
            verdict=None if trial.verdict is None else trial.verdict.value,
            note=trial.note,
        )

    @staticmethod
    def _trial(record: TrialRecord) -> Trial:
        return Trial(
            trial_id=record.id,
            experiment=record.experiment,
            strategy=record.strategy,
            candidate=record.candidate,
            role=TrialRole(record.role),
            dataset_version=record.dataset_version,
            config_hash=record.config_hash,
            cost_model=record.cost_model,
            recorded_at=record.recorded_at,
            run_id=record.run_id,
            trade_count=record.trade_count,
            net_pnl=None if record.net_pnl is None else record.net_pnl.amount,
            daily_sharpe=record.daily_sharpe,
            verdict=None if record.verdict is None else Verdict(record.verdict),
            note=record.note,
        )
