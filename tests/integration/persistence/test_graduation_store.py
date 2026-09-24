"""`MongoGraduationLedger` and `MongoAcknowledgementBook` on real Atlas (EM-189): the unique
`(strategy, seq)` index refuses a racing duplicate and acknowledgement uniqueness holds. Every
strategy is made up (`em189-test-*`) and removed afterwards; no real strategy is ever touched."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.domain.graduation import (
    EvidenceKind,
    EvidenceRef,
    GraduationEvent,
    GraduationStage,
    LiveAcknowledgement,
    TransitionKind,
    acknowledgement_phrase,
)
from emporos.persistence.collections import Collection
from emporos.persistence.graduation_store import (
    AcknowledgementExistsError,
    GraduationConflictError,
    MongoAcknowledgementBook,
    MongoGraduationLedger,
)
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA, Schema

pytestmark = pytest.mark.integration

STRATEGY = "em189-test-strategy"
HASH = "abcdef0123456789"
AT = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
S = GraduationStage


def event(seq: int, origin: GraduationStage, target: GraduationStage, kind: TransitionKind):  # type: ignore[no-untyped-def]
    evidence = (
        (EvidenceRef(EvidenceKind.VERDICT, "v-1", "validated"),)
        if kind is TransitionKind.PROMOTE
        else ()
    )
    return GraduationEvent(STRATEGY, HASH, seq, origin, target, kind, evidence, "rama", "test", AT)


@pytest.fixture
async def stores(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[tuple[MongoGraduationLedger, MongoAcknowledgementBook]]:
    only = Schema(
        (
            PLATFORM_SCHEMA.spec_for(Collection.GRADUATION_EVENTS),
            PLATFORM_SCHEMA.spec_for(Collection.LIVE_ACKNOWLEDGEMENTS),
        )
    )
    await MigrationRunner(MongoSchemaStore(database), only).apply()
    try:
        yield (
            MongoGraduationLedger(database, IdGenerator()),
            MongoAcknowledgementBook(database, IdGenerator()),
        )
    finally:
        for name in (Collection.GRADUATION_EVENTS, Collection.LIVE_ACKNOWLEDGEMENTS):
            await database[name].delete_many({"strategy": STRATEGY})


async def test_events_round_trip_in_order_and_the_latest_is_the_highest_seq(stores) -> None:  # type: ignore[no-untyped-def]
    ledger, _ = stores
    first = event(1, S.RESEARCH, S.PAPER, TransitionKind.PROMOTE)
    second = event(2, S.PAPER, S.RESEARCH, TransitionKind.DEMOTE)
    await ledger.append(first)
    await ledger.append(second)

    assert await ledger.history(STRATEGY) == [first, second]
    assert await ledger.latest(STRATEGY) == second
    assert await ledger.latest("em189-test-nobody") is None


async def test_a_second_event_with_the_same_seq_is_refused(stores) -> None:  # type: ignore[no-untyped-def]
    ledger, _ = stores
    await ledger.append(event(1, S.RESEARCH, S.PAPER, TransitionKind.PROMOTE))

    with pytest.raises(GraduationConflictError):
        await ledger.append(event(1, S.RESEARCH, S.PAPER, TransitionKind.PROMOTE))

    assert len(await ledger.history(STRATEGY)) == 1


async def test_an_acknowledgement_round_trips_and_is_recorded_once(stores) -> None:  # type: ignore[no-untyped-def]
    _, book = stores
    ack = LiveAcknowledgement(
        STRATEGY, HASH, "rama", acknowledgement_phrase(STRATEGY, HASH), "live_conservative", AT
    )
    assert await book.get(STRATEGY, HASH) is None

    await book.record(ack)

    assert await book.get(STRATEGY, HASH) == ack
    assert await book.get(STRATEGY, "1" * 16) is None
    with pytest.raises(AcknowledgementExistsError):
        await book.record(ack)
