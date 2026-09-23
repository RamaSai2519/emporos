"""`MongoHypothesisRegistry` on real Atlas (EM-178). `em178-test-h1` is a made-up hypothesis id
that never appears in real research, so the shared dev database is never polluted for real runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.hypotheses import DuplicateHypothesisError, HypothesisDeclaration
from emporos.persistence.collections import Collection
from emporos.persistence.hypothesis_store import MongoHypothesisRegistry
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA

pytestmark = pytest.mark.integration

HYPOTHESIS_ID = "em178-test-h1"
DECLARED = datetime(2026, 3, 5, tzinfo=UTC)


def declaration() -> HypothesisDeclaration:
    return HypothesisDeclaration(
        HYPOTHESIS_ID, "momentum", "v1", date(2026, 1, 1), date(2026, 3, 1),
        date(2026, 2, 1), date(2026, 3, 1), DECLARED,
    )  # fmt: skip


@pytest.fixture
async def registry(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[MongoHypothesisRegistry]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    try:
        yield MongoHypothesisRegistry(database)
    finally:
        await database[Collection.HYPOTHESIS_REGISTRY].delete_many({"_id": HYPOTHESIS_ID})


async def test_a_declaration_round_trips(registry: MongoHypothesisRegistry) -> None:
    await registry.declare(declaration())

    found = await registry.get(HYPOTHESIS_ID)

    assert found == declaration()


async def test_an_unknown_id_returns_none(registry: MongoHypothesisRegistry) -> None:
    assert await registry.get("missing") is None


async def test_declaring_the_same_id_twice_is_refused_not_overwritten(
    registry: MongoHypothesisRegistry,
) -> None:
    await registry.declare(declaration())

    with pytest.raises(DuplicateHypothesisError):
        await registry.declare(declaration())
