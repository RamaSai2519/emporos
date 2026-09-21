"""Which cold tier a deployment has: S3 when a bucket is configured, else a local directory
(`COLD_ARCHIVE_DIR`, default `~/.local/share/emporos/cold`). One place decides, so history, rollup
and backtests agree on where old bars are. History is Parquet files, never Mongo documents."""

from __future__ import annotations

from pathlib import Path

from emporos.core.config import Settings
from emporos.persistence.candle_cold import ColdCandleArchive, ParquetCandleArchive
from emporos.persistence.file_object_store import FileObjectStore
from emporos.persistence.object_store import ObjectStore, S3ClientFactory

DEFAULT_COLD_DIR = Path.home() / ".local" / "share" / "emporos" / "cold"


def cold_object_store(settings: Settings) -> ObjectStore:
    if settings.s3_bucket:
        return S3ClientFactory(settings).create_store()
    return FileObjectStore(Path(settings.cold_archive_dir or DEFAULT_COLD_DIR))


def cold_archive(settings: Settings) -> ColdCandleArchive:
    return ParquetCandleArchive(cold_object_store(settings))
