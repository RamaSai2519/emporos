"""A local-disk read-through cache in front of any `ObjectStore`.

Backtests re-read the same cold Parquet months repeatedly; this keeps them off
the network. Freshness is by etag: every `get` does one cheap `stat` and serves
the cached bytes only while the etag is unchanged, so a re-archived month is
never served stale. Cache files are named `{sha256(key)}-{etag}`; superseded
versions of a key are removed when a new one is cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from emporos.persistence.object_store import ObjectInfo, ObjectNotFoundError, ObjectStore


class DiskCachingObjectStore:
    def __init__(self, inner: ObjectStore, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes) -> None:
        await self._inner.put(key, data)

    async def get(self, key: str) -> bytes:
        info = await self._inner.stat(key)
        if info is None:
            raise ObjectNotFoundError(key)
        cached = self._path(key, info.etag)
        if cached.is_file():
            return await asyncio.to_thread(cached.read_bytes)
        data = await self._inner.get(key)
        await asyncio.to_thread(self._store, key, cached, data)
        return data

    async def stat(self, key: str) -> ObjectInfo | None:
        return await self._inner.stat(key)

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return await self._inner.list_objects(prefix)

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)

    def _path(self, key: str, etag: str) -> Path:
        return self._directory / f"{self._digest(key)}-{etag}"

    @staticmethod
    def _digest(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def _store(self, key: str, target: Path, data: bytes) -> None:
        for stale in self._directory.glob(f"{self._digest(key)}-*"):
            stale.unlink(missing_ok=True)
        scratch = target.with_suffix(f".tmp{os.getpid()}")
        scratch.write_bytes(data)
        scratch.replace(target)  # atomic: readers never see a partial file
