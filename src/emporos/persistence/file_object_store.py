"""An `ObjectStore` on the local filesystem: the cold tier without a bucket.

Same contract as `S3ObjectStore` (so `ParquetCandleArchive` runs on it unchanged), keyed by
relative paths under one root directory. A write goes to a temporary file and is renamed into place,
so a reader never sees half an object and a crash mid-write leaves the old object intact.
Blocking file calls are moved off the event loop, as the S3 adapter's boto3 calls are.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from emporos.persistence.object_store import ObjectInfo, ObjectNotFoundError, ObjectStoreError


class FileObjectStore:
    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._put, key, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get, key)

    async def stat(self, key: str) -> ObjectInfo | None:
        return await asyncio.to_thread(self._stat, key)

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return await asyncio.to_thread(self._list, prefix)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete, key)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise ObjectStoreError(f"object key '{key}' escapes the store")
        return path

    def _put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError as error:
            raise ObjectStoreError(f"could not write '{key}': {error}") from error

    def _get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise ObjectNotFoundError(key) from None
        except OSError as error:
            raise ObjectStoreError(f"could not read '{key}': {error}") from error

    def _stat(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return self._info(key, path)

    def _list(self, prefix: str) -> list[ObjectInfo]:
        if not self._root.is_dir():
            return []
        found = (
            self._info(path.relative_to(self._root).as_posix(), path)
            for path in self._root.rglob("*")
            if path.is_file() and not path.name.endswith(".tmp")
        )
        return sorted((i for i in found if i.key.startswith(prefix)), key=lambda i: i.key)

    def _delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    @staticmethod
    def _info(key: str, path: Path) -> ObjectInfo:
        return ObjectInfo(key, path.stat().st_size, hashlib.md5(path.read_bytes()).hexdigest())
