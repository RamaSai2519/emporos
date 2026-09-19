"""The behaviour every `ObjectStore` must have. Subclass and override the `store` fixture."""

from __future__ import annotations

import pytest

from emporos.persistence.object_store import ObjectNotFoundError, ObjectStore


class ObjectStoreContract:
    @pytest.fixture
    def store(self) -> ObjectStore:
        raise NotImplementedError

    async def test_put_then_get_round_trips_bytes(self, store: ObjectStore) -> None:
        await store.put("a/b.bin", b"\x00\x01payload")

        assert await store.get("a/b.bin") == b"\x00\x01payload"

    async def test_put_overwrites(self, store: ObjectStore) -> None:
        await store.put("k", b"one")
        await store.put("k", b"two")

        assert await store.get("k") == b"two"

    async def test_get_of_a_missing_key_raises_not_found(self, store: ObjectStore) -> None:
        with pytest.raises(ObjectNotFoundError) as raised:
            await store.get("missing")

        assert raised.value.key == "missing"

    async def test_stat_reports_size_and_a_content_dependent_etag(self, store: ObjectStore) -> None:
        await store.put("k", b"abc")
        first = await store.stat("k")
        await store.put("k", b"abcd")
        second = await store.stat("k")

        assert first is not None and second is not None
        assert (first.size, second.size) == (3, 4)
        assert first.etag != second.etag

    async def test_stat_of_a_missing_key_is_none(self, store: ObjectStore) -> None:
        assert await store.stat("missing") is None

    async def test_list_returns_only_the_prefix_in_key_order(self, store: ObjectStore) -> None:
        for key in ("x/2", "x/1", "y/1"):
            await store.put(key, key.encode())

        listed = await store.list_objects("x/")

        assert [info.key for info in listed] == ["x/1", "x/2"]
        assert [info.size for info in listed] == [3, 3]

    async def test_list_of_an_empty_prefix_is_empty(self, store: ObjectStore) -> None:
        assert await store.list_objects("nothing/") == []

    async def test_delete_removes_and_is_idempotent(self, store: ObjectStore) -> None:
        await store.put("k", b"v")

        await store.delete("k")
        await store.delete("k")

        assert await store.stat("k") is None
