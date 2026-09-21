"""EM-139: recorded verdicts on real Atlas. Uses the real collection with strategy names unique to
each test, and deletes only its own rows (the dev database is shared)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import GateFinding, RecordedVerdict
from emporos.persistence.collections import Collection
from emporos.persistence.verdict_store import MongoVerdictBook

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)


def verdict(strategy: str, outcome: Verdict, minute: int = 0) -> RecordedVerdict:
    return RecordedVerdict(
        strategy=strategy,
        behaviour_hash="sha256:abc",
        verdict=outcome,
        gates=(
            GateFinding("profit after costs is real", "fail", "net -3136.27"),
            GateFinding("drawdown within the budget", "pass", "3.1% of 10%"),
        ),
        capital="50000",
        first_day="2025-09-22",
        last_day="2026-09-18",
        experiment="it",
        source="curation",
        recorded_at=T0 + timedelta(minutes=minute),
        notes=("judged at a smaller size",),
    )


@pytest.fixture
async def book(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[tuple[MongoVerdictBook, str]]:
    strategy = "zz_verdict_" + IdGenerator().new_ulid().lower()
    try:
        yield MongoVerdictBook(database), strategy
    finally:
        await database[Collection.STRATEGY_VERDICTS].delete_many({"strategy": strategy})


async def test_a_verdict_round_trips_with_its_gates_and_notes(book: Any) -> None:
    store, strategy = book
    written = verdict(strategy, Verdict.REJECTED)

    await store.append(written, IdGenerator().new_ulid())

    assert await store.latest(strategy) == written


async def test_the_latest_verdict_is_the_one_that_applies_and_the_old_one_is_kept(
    book: Any, database: AsyncDatabase[Mapping[str, Any]]
) -> None:
    store, strategy = book
    await store.append(verdict(strategy, Verdict.REJECTED, minute=0), IdGenerator().new_ulid())
    await store.append(verdict(strategy, Verdict.INCONCLUSIVE, minute=5), IdGenerator().new_ulid())

    latest = await store.latest(strategy)

    assert latest is not None and latest.verdict is Verdict.INCONCLUSIVE
    assert await database[Collection.STRATEGY_VERDICTS].count_documents({"strategy": strategy}) == 2
    assert (await store.latest_of_each())[strategy].verdict is Verdict.INCONCLUSIVE


async def test_a_strategy_never_curated_has_no_verdict(book: Any) -> None:
    store, strategy = book

    assert await store.latest(strategy) is None
    assert strategy not in await store.latest_of_each()


async def test_a_verdict_id_is_never_overwritten(book: Any) -> None:
    from emporos.persistence.errors import DuplicateRecordError

    store, strategy = book
    verdict_id = IdGenerator().new_ulid()
    await store.append(verdict(strategy, Verdict.REJECTED), verdict_id)

    with pytest.raises(DuplicateRecordError):
        await store.append(verdict(strategy, Verdict.VALIDATED, minute=1), verdict_id)
    latest = await store.latest(strategy)
    assert latest is not None and latest.verdict is Verdict.REJECTED


def test_the_store_offers_no_way_to_edit_or_delete() -> None:
    public = {n for n in dir(MongoVerdictBook) if not n.startswith("_")}

    assert public == {"append", "latest", "latest_of_each"}
