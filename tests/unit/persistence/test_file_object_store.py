"""EM-132: the local-directory cold tier honours the same object-store contract S3 does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.file_object_store import FileObjectStore
from emporos.persistence.object_store import ObjectNotFoundError, ObjectStoreError


async def test_an_object_round_trips(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)

    await store.put("candles/5m/NSE_1/2026-03.parquet", b"hello")

    assert await store.get("candles/5m/NSE_1/2026-03.parquet") == b"hello"
    info = await store.stat("candles/5m/NSE_1/2026-03.parquet")
    assert info is not None and info.size == 5


async def test_a_missing_object_is_reported_as_missing_not_as_a_failure(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)

    with pytest.raises(ObjectNotFoundError):
        await store.get("nope")
    assert await store.stat("nope") is None
    await store.delete("nope")  # deleting nothing is fine


async def test_a_put_replaces_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)
    await store.put("a/b.bin", b"one")

    await store.put("a/b.bin", b"three")

    assert await store.get("a/b.bin") == b"three"
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["b.bin"]


async def test_listing_is_by_prefix_and_sorted(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)
    for key in ("candles/5m/x/2.parquet", "candles/5m/x/1.parquet", "coverage/5m/x/1.json"):
        await store.put(key, b"1")

    listed = await store.list_objects("candles/")

    assert [i.key for i in listed] == ["candles/5m/x/1.parquet", "candles/5m/x/2.parquet"]
    assert await FileObjectStore(tmp_path / "empty").list_objects("") == []


async def test_a_key_cannot_climb_out_of_the_store(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path / "root")

    with pytest.raises(ObjectStoreError, match="escapes"):
        await store.put("../outside.bin", b"x")
    assert not (tmp_path / "outside.bin").exists()


async def test_delete_removes_the_object(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)
    await store.put("k", b"1")

    await store.delete("k")

    assert await store.stat("k") is None


async def test_the_parquet_archive_runs_on_it_unchanged(tmp_path: Path) -> None:
    archive = ParquetCandleArchive(FileObjectStore(tmp_path))
    first = datetime(2021, 3, 2, 3, 45, tzinfo=UTC)
    price = Money(Decimal("100.5"))
    bars = [
        Candle(
            "NSE:1", Timeframe.M5, first + timedelta(minutes=5 * n), price, price, price, price, n
        )
        for n in range(4)
    ]

    await archive.archive(bars)
    await archive.archive(bars)  # merging the same bars again changes nothing

    back = await archive.read("NSE:1", Timeframe.M5, first, first + timedelta(days=1))
    assert back == bars
    assert len(list(tmp_path.rglob("*.parquet"))) == 1
