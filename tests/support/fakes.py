"""Lightweight test doubles that implement the same Protocols as the production adapters."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from emporos.persistence.migrations import IndexInfo
from emporos.persistence.object_store import ObjectInfo, ObjectNotFoundError
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


class InMemoryObjectStore:
    """`ObjectStore` double backed by a dict; etags change whenever the bytes do."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.get_calls = 0

    async def put(self, key: str, data: bytes) -> None:
        self._objects[key] = data

    async def get(self, key: str) -> bytes:
        self.get_calls += 1
        if key not in self._objects:
            raise ObjectNotFoundError(key)
        return self._objects[key]

    async def stat(self, key: str) -> ObjectInfo | None:
        data = self._objects.get(key)
        return None if data is None else self._info(key, data)

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return [
            self._info(key, data)
            for key, data in sorted(self._objects.items())
            if key.startswith(prefix)
        ]

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    @staticmethod
    def _info(key: str, data: bytes) -> ObjectInfo:
        return ObjectInfo(key, len(data), hashlib.md5(data, usedforsecurity=False).hexdigest())
