"""Idempotent schema migration: create every collection and index in a `Schema`.

The runner is declarative — it compares the declared schema to what the store
reports and creates only what is missing, so `emporos db migrate` is safe to
re-run at any time. An existing index that disagrees with its declaration is
never silently altered: it raises `SchemaDriftError` for an explicit decision.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from emporos.persistence.errors import SchemaDriftError
from emporos.persistence.schema import CollectionSpec, IndexSpec, Schema

_DEFAULT_ID_INDEX = "_id_"


@dataclass(frozen=True)
class IndexInfo:
    """What the store reports about one existing index."""

    keys: tuple[tuple[str, int], ...]
    unique: bool
    expire_after_seconds: int | None


class SchemaStore(Protocol):
    """The slice of the database the migration runner needs."""

    async def collection_names(self) -> set[str]: ...

    async def create_collection(self, spec: CollectionSpec) -> None: ...

    async def indexes(self, collection: str) -> Mapping[str, IndexInfo]: ...

    async def create_index(self, collection: str, index: IndexSpec) -> None: ...


@dataclass(frozen=True)
class MigrationReport:
    created_collections: tuple[str, ...]
    created_indexes: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.created_collections or self.created_indexes)

    def summary(self) -> str:
        if not self.changed:
            return "schema already up to date"
        return (
            f"created {len(self.created_collections)} collection(s) and "
            f"{len(self.created_indexes)} index(es)"
        )


class MigrationRunner:
    def __init__(self, store: SchemaStore, schema: Schema) -> None:
        self._store = store
        self._schema = schema

    async def apply(self) -> MigrationReport:
        existing = await self._store.collection_names()
        created_collections: list[str] = []
        created_indexes: list[str] = []
        for spec in self._schema.collections:
            if spec.name not in existing:
                await self._store.create_collection(spec)
                created_collections.append(spec.name)
            created_indexes.extend(await self._apply_indexes(spec))
        return MigrationReport(tuple(created_collections), tuple(created_indexes))

    async def missing_indexes(self) -> tuple[str, ...]:
        """`collection.index` for every declared index the store does not have."""
        existing = await self._store.collection_names()
        missing: list[str] = []
        for spec in self._schema.collections:
            present = await self._store.indexes(spec.name) if spec.name in existing else {}
            missing.extend(
                f"{spec.name}.{index.name}" for index in spec.indexes if index.name not in present
            )
        return tuple(missing)

    async def _apply_indexes(self, spec: CollectionSpec) -> list[str]:
        present = await self._store.indexes(spec.name)
        created: list[str] = []
        for index in spec.indexes:
            found = present.get(index.name)
            if found is None:
                await self._store.create_index(spec.name, index)
                created.append(f"{spec.name}.{index.name}")
            else:
                self._require_matches(spec.name, index, found)
        return created

    @staticmethod
    def _require_matches(collection: str, declared: IndexSpec, found: IndexInfo) -> None:
        if (
            found.keys != declared.keys
            or found.unique != declared.unique
            or found.expire_after_seconds != declared.expire_after_seconds
        ):
            raise SchemaDriftError(
                f"index '{collection}.{declared.name}' exists but differs from the declared "
                f"schema (unique={found.unique}, ttl={found.expire_after_seconds}); "
                "resolve it manually"
            )


class MongoSchemaStore:
    """`SchemaStore` backed by a real MongoDB database."""

    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self._database = database

    async def collection_names(self) -> set[str]:
        return set(await self._database.list_collection_names())

    async def create_collection(self, spec: CollectionSpec) -> None:
        options: dict[str, Any] = {}
        if spec.timeseries is not None:
            options["timeseries"] = {
                "timeField": spec.timeseries.time_field,
                "metaField": spec.timeseries.meta_field,
                "granularity": spec.timeseries.granularity,
            }
            options["expireAfterSeconds"] = int(spec.timeseries.expire_after.total_seconds())
        # A concurrent migrate may create it first — the goal state is reached either way.
        with contextlib.suppress(CollectionInvalid):
            await self._database.create_collection(spec.name, **options)

    async def indexes(self, collection: str) -> Mapping[str, IndexInfo]:
        raw = await self._database[collection].index_information()
        return {
            name: IndexInfo(
                keys=tuple((field, int(direction)) for field, direction in info["key"]),
                unique=bool(info.get("unique", False)),
                expire_after_seconds=info.get("expireAfterSeconds"),
            )
            for name, info in raw.items()
            if name != _DEFAULT_ID_INDEX
        }

    async def create_index(self, collection: str, index: IndexSpec) -> None:
        options: dict[str, Any] = {"name": index.name, "unique": index.unique}
        if index.expire_after_seconds is not None:
            options["expireAfterSeconds"] = index.expire_after_seconds
        await self._database[collection].create_index(list(index.keys), **options)
