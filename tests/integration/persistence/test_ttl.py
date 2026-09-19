"""TTL indexes are configured as declared, and Atlas really expires documents (EM-24).

Expiry is checked on a throwaway collection (never a real one) because Mongo's TTL monitor
runs about once a minute, so the functioning test waits for a sweep.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA

pytestmark = pytest.mark.integration

_TTL_INDEXES = [
    (spec.name, index)
    for spec in PLATFORM_SCHEMA.collections
    for index in spec.indexes
    if index.expire_after is not None
]
_SWEEP_TIMEOUT_SECONDS = 180


@pytest.mark.parametrize(
    ("collection", "index"), _TTL_INDEXES, ids=[f"{c}.{i.name}" for c, i in _TTL_INDEXES]
)
async def test_every_declared_ttl_is_configured_on_atlas(
    database: AsyncDatabase[Mapping[str, Any]], collection: str, index: Any
) -> None:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()

    present = await MongoSchemaStore(database).indexes(collection)

    assert present[index.name].expire_after_seconds == index.expire_after_seconds


async def test_a_ttl_index_actually_expires_old_documents(
    database: AsyncDatabase[Mapping[str, Any]],
) -> None:
    scratch = database[f"zz_ttl_{IdGenerator().new_ulid().lower()}"]
    await scratch.create_index("ts", expireAfterSeconds=1)
    await scratch.insert_one({"ts": datetime.now(UTC) - timedelta(minutes=5)})
    try:
        deadline = time.monotonic() + _SWEEP_TIMEOUT_SECONDS
        while await scratch.count_documents({}) > 0:
            assert time.monotonic() < deadline, "TTL monitor did not expire the document in time"
            await asyncio.sleep(5)
    finally:
        await scratch.drop()
