"""Lightweight test doubles that implement the same Protocols as the production adapters."""

from __future__ import annotations

from collections.abc import Mapping

from emporos.persistence.migrations import IndexInfo
from emporos.persistence.schema import CollectionSpec, IndexSpec


class InMemorySchemaStore:
    """`SchemaStore` double: remembers collections and indexes in plain dicts."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, IndexInfo]] = {}

    async def collection_names(self) -> set[str]:
        return set(self._collections)

    async def create_collection(self, spec: CollectionSpec) -> None:
        self._collections.setdefault(spec.name, {})

    async def indexes(self, collection: str) -> Mapping[str, IndexInfo]:
        return dict(self._collections[collection])

    async def create_index(self, collection: str, index: IndexSpec) -> None:
        self._collections[collection][index.name] = IndexInfo(
            keys=index.keys,
            unique=index.unique,
            expire_after_seconds=index.expire_after_seconds,
        )

    def drop_index(self, collection: str, name: str) -> None:
        del self._collections[collection][name]

    def replace_index(self, collection: str, name: str, info: IndexInfo) -> None:
        self._collections[collection][name] = info
