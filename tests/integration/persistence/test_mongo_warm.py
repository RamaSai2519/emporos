"""EM-99 H1: one awaited round trip opens the pool, after which concurrent first use is safe."""

import asyncio
from typing import Any

import pytest

from emporos.persistence.mongo import MongoClientFactory

pytestmark = pytest.mark.integration


async def test_a_warmed_client_serves_concurrent_first_operations(dev_settings: Any) -> None:
    mongo = MongoClientFactory(dev_settings)
    try:
        await mongo.warm()
        results = await asyncio.wait_for(
            asyncio.gather(*(mongo.database().command("ping") for _ in range(8))), timeout=30
        )
        assert all(r["ok"] == 1.0 for r in results)
    finally:
        await mongo.close()
