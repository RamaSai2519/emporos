"""Copy a recorded day's Parquet files to an object store, once each (EM-236).

Files are immutable (new part files only), so a key that exists with the same size is done and
is skipped: running the upload twice, or after a restart that wrote more parts, sends only what
is new."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

__all__ = ["DayUpload", "DayUploader", "RemoteFiles", "RemoteStat"]

_LOG = logging.getLogger(__name__)


class RemoteStat(Protocol):
    @property
    def size(self) -> int: ...


class RemoteFiles(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def stat(self, key: str) -> RemoteStat | None: ...


@dataclass(frozen=True)
class DayUpload:
    uploaded: int
    skipped: int
    failed: int


class DayUploader:
    def __init__(self, store: RemoteFiles, root: Path, prefix: str = "quotes") -> None:
        self._store, self._root, self._prefix = store, root, prefix.strip("/")

    async def upload(self, day: date) -> DayUpload:
        directory = self._root / f"date={day.isoformat()}"
        files = sorted(directory.glob("part-*.parquet")) if directory.exists() else []
        uploaded = skipped = failed = 0
        for path in files:
            key = f"{self._prefix}/date={day.isoformat()}/{path.name}"
            try:
                data = path.read_bytes()
                present = await self._store.stat(key)
                if present is not None and present.size == len(data):
                    skipped += 1
                    continue
                await self._store.put(key, data)
                uploaded += 1
            except Exception as error:  # one bad file must not stop the rest
                failed += 1
                _LOG.error("quote upload failed for %s: %s", key, error)
        return DayUpload(uploaded, skipped, failed)
