"""A throwaway instrument master on real Atlas: scratch collections, dropped afterwards."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.config import Settings
from emporos.core.ids import IdGenerator
from emporos.instruments.store import MongoInstrumentMasterStore
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.records import InstrumentRecord
from emporos.persistence.repositories import InstrumentRepository, InstrumentVersionRepository
from emporos.persistence.schema import PLATFORM_SCHEMA, CollectionSpec, Schema
from emporos.persistence.staged_load import StagedCollectionLoader
from emporos.persistence.transactions import TransactionRunner


@dataclass
class Rig:
    database: AsyncDatabase[Mapping[str, Any]]
    store: MongoInstrumentMasterStore
    instruments: InstrumentRepository
    versions: InstrumentVersionRepository
    schema: Schema
    instruments_name: str

    @property
    def staging_name(self) -> str:
        return f"{self.instruments_name}__staging"


async def scratch_rig(settings: Settings) -> AsyncIterator[Rig]:
    """Yield a `Rig` over fresh scratch collections; drop them on exit."""
    mongo = MongoClientFactory(settings)
    database: AsyncDatabase[Mapping[str, Any]] = mongo.database()
    suffix = IdGenerator().new_ulid().lower()
    instruments_name, versions_name = f"zz_instruments_{suffix}", f"zz_versions_{suffix}"
    instruments_spec = CollectionSpec(
        instruments_name, PLATFORM_SCHEMA.spec_for(Collection.INSTRUMENTS).indexes
    )
    schema = Schema(
        (
            instruments_spec,
            CollectionSpec(
                versions_name, PLATFORM_SCHEMA.spec_for(Collection.INSTRUMENT_VERSIONS).indexes
            ),
        )
    )
    await MigrationRunner(MongoSchemaStore(database), schema).apply()
    instruments = InstrumentRepository(database, instruments_name)
    versions = InstrumentVersionRepository(database, versions_name)
    store = MongoInstrumentMasterStore(
        TransactionRunner(mongo.client),
        instruments,
        versions,
        StagedCollectionLoader(database, instruments_spec, InstrumentRecord),
    )
    try:
        yield Rig(database, store, instruments, versions, schema, instruments_name)
    finally:
        for name in (instruments_name, versions_name, f"{instruments_name}__staging"):
            await database[name].drop()
        await mongo.close()
