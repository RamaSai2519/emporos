"""Bulk-replace a collection atomically: build the new contents off to the side, then swap.

A transaction cannot hold a large bulk load (Atlas caps transaction lifetime at
about a minute), and loading in place would expose a half-built collection to
readers and to a crash. Instead the records are loaded into a staging collection
with the target's exact indexes, and one `rename(dropTarget=True)` swaps it in —
atomic for readers, and a crash before the rename leaves the target untouched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Generic, TypeVar

from pymongo.asynchronous.database import AsyncDatabase

from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.records import Record
from emporos.persistence.repository import Repository
from emporos.persistence.schema import CollectionSpec, Schema

R = TypeVar("R", bound=Record)
_BATCH = 1000
_STAGING_SUFFIX = "__staging"


class StagedCollectionLoader(Generic[R]):
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        target: CollectionSpec,
        record_type: type[R],
    ) -> None:
        self._database = database
        self._target = target
        self._record_type = record_type

    async def replace_all(self, records: Sequence[R]) -> None:
        staging = f"{self._target.name}{_STAGING_SUFFIX}"
        await self._database.drop_collection(staging)  # a crashed earlier run may have left one
        try:
            await MigrationRunner(
                MongoSchemaStore(self._database),
                Schema((CollectionSpec(staging, self._target.indexes),)),
            ).apply()
            repository = Repository(self._database, staging, self._record_type)
            for start in range(0, len(records), _BATCH):
                await repository.insert_many(records[start : start + _BATCH])
            await self._database[staging].rename(self._target.name, dropTarget=True)
        except BaseException:
            await self._database.drop_collection(staging)
            raise
