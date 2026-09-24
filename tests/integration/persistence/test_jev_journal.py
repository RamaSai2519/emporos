"""`MongoJevDecisionJournal` on real Atlas (EM-187): the unique index and append-only behaviour.
Every request hash is made up (`em187-test-*`) and removed afterwards, so the shared dev database
is never polluted for real runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.jev_records import JevDecisionRecord
from emporos.persistence.collections import Collection
from emporos.persistence.jev_journal import MongoJevDecisionJournal
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA, Schema

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 5, 4, 0, tzinfo=UTC)
PREFIX = "em187-test-"


def record(request_hash: str = PREFIX + "a", model: str = "vendor/model-a") -> JevDecisionRecord:
    return JevDecisionRecord(
        request_hash=request_hash,
        as_of=NOW,
        symbol="INSTR_ab12cd34",
        strategy="momentum_v1",
        mode="confirmation",
        decision="confirm",
        confidence=Decimal("0.80"),
        provider="vercel_gateway",
        model=model,
        prompt_version="v1",
        prompt_hash=PREFIX + "prompt",
        tokens_used=120,
        latency_ms=300,
        recorded_at=NOW,
    )


@pytest.fixture
async def journal(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[MongoJevDecisionJournal]:
    # Only this collection's spec: the shared dev database has unrelated index drift elsewhere.
    only = Schema((PLATFORM_SCHEMA.spec_for(Collection.JEV_DECISIONS),))
    await MigrationRunner(MongoSchemaStore(database), only).apply()
    try:
        yield MongoJevDecisionJournal(database)
    finally:
        await database[Collection.JEV_DECISIONS].delete_many(
            {"request_hash": {"$regex": f"^{PREFIX}"}}
        )


async def test_a_record_round_trips(journal: MongoJevDecisionJournal) -> None:
    assert await journal.append(record()) is True

    stored = await journal.get(PREFIX + "a", PREFIX + "prompt", "vendor/model-a")
    assert stored == record()


async def test_the_same_question_is_not_written_twice(journal: MongoJevDecisionJournal) -> None:
    assert await journal.append(record()) is True
    assert await journal.append(replace(record(), decision="reject")) is False

    stored = await journal.get(PREFIX + "a", PREFIX + "prompt", "vendor/model-a")
    assert stored is not None and stored.decision == "confirm"  # the first stands


async def test_a_different_model_is_a_different_question(journal: MongoJevDecisionJournal) -> None:
    assert await journal.append(record(model="vendor/model-a")) is True
    assert await journal.append(record(model="vendor/model-b")) is True

    assert await journal.get(PREFIX + "a", PREFIX + "prompt", "vendor/model-b") is not None


async def test_an_unknown_question_is_none(journal: MongoJevDecisionJournal) -> None:
    assert await journal.get(PREFIX + "missing", PREFIX + "prompt", "vendor/model-a") is None


async def test_the_records_of_one_model_and_prompt_are_listed_in_time_order(
    journal: MongoJevDecisionJournal,
) -> None:
    later = replace(record(PREFIX + "b"), as_of=NOW.replace(hour=5))
    await journal.append(later)
    await journal.append(record(PREFIX + "a"))
    await journal.append(record(PREFIX + "c", model="vendor/model-b"))

    listed = await journal.records("vendor/model-a", PREFIX + "prompt")

    assert [r.request_hash for r in listed if r.request_hash.startswith(PREFIX)] == [
        PREFIX + "a",
        PREFIX + "b",
    ]
