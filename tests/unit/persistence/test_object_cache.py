from pathlib import Path

import pytest

from emporos.persistence.object_cache import DiskCachingObjectStore
from emporos.persistence.object_store import ObjectNotFoundError, ObjectStore
from tests.contract.object_store_contract import ObjectStoreContract
from tests.support.fakes import InMemoryObjectStore


class TestDiskCachingObjectStoreHonoursTheObjectStoreContract(ObjectStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> ObjectStore:
        return DiskCachingObjectStore(InMemoryObjectStore(), tmp_path / "cache")


async def test_a_second_read_is_served_from_disk(tmp_path: Path) -> None:
    inner = InMemoryObjectStore()
    cache = DiskCachingObjectStore(inner, tmp_path)
    await inner.put("k", b"payload")

    assert await cache.get("k") == b"payload"
    assert await cache.get("k") == b"payload"

    assert inner.get_calls == 1


async def test_a_changed_object_is_never_served_stale(tmp_path: Path) -> None:
    inner = InMemoryObjectStore()
    cache = DiskCachingObjectStore(inner, tmp_path)
    await cache.put("k", b"old")
    assert await cache.get("k") == b"old"

    await cache.put("k", b"new")

    assert await cache.get("k") == b"new"
    assert len(list(tmp_path.iterdir())) == 1  # the superseded version was removed


async def test_reading_a_missing_object_raises_not_found(tmp_path: Path) -> None:
    cache = DiskCachingObjectStore(InMemoryObjectStore(), tmp_path)

    with pytest.raises(ObjectNotFoundError):
        await cache.get("missing")
