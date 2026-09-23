"""`MongoParityReportStore` on real Atlas (EM-185): the unique index, idempotent append, and
`latest()` ordering. Every id and strategy is made up (`em185-test-*`) and removed afterwards, so
the shared dev database is never polluted for real runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.experiments import Verdict
from emporos.domain.parity import ParityKind, ParityReport
from emporos.domain.verdicts import GateFinding
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.parity_store import MongoParityReportStore
from emporos.persistence.schema import PLATFORM_SCHEMA, Schema

pytestmark = pytest.mark.integration

STRATEGY = "em185-test-strategy"
HASH = "sha256:em185-test"
NOW = datetime(2026, 3, 5, tzinfo=UTC)


def report(
    kind: ParityKind = ParityKind.CUMULATIVE,
    first: date = date(2026, 3, 2),
    last: date = date(2026, 3, 5),
    verdict: Verdict = Verdict.INCONCLUSIVE,
    recorded_at: datetime = NOW,
) -> ParityReport:
    return ParityReport(
        STRATEGY, HASH, kind, first, last, verdict,
        (GateFinding("enough paper sessions", "unknown", "4 session(s), 10 needed"),),
        4, 7,
        {"fill_rate": {"backtest": "1", "paper": "0.9", "absolute": "-0.1", "relative": None}},
        recorded_at, {"session": {"k": "v"}},
    )  # fmt: skip


@pytest.fixture
async def store(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[MongoParityReportStore]:
    # Only this collection's spec: the shared dev database has unrelated index drift elsewhere.
    only = Schema((PLATFORM_SCHEMA.spec_for(Collection.PARITY_REPORTS),))
    await MigrationRunner(MongoSchemaStore(database), only).apply()
    try:
        yield MongoParityReportStore(database)
    finally:
        await database[Collection.PARITY_REPORTS].delete_many({"strategy": STRATEGY})


async def test_a_report_round_trips(store: MongoParityReportStore) -> None:
    assert await store.append(report(), "em185-test-1") is True

    assert await store.latest(STRATEGY, HASH) == report()


async def test_the_same_span_and_kind_is_not_written_twice(store: MongoParityReportStore) -> None:
    assert await store.append(report(), "em185-test-1") is True
    assert await store.append(report(verdict=Verdict.REJECTED), "em185-test-2") is False

    latest = await store.latest(STRATEGY, HASH)
    assert latest is not None and latest.verdict is Verdict.INCONCLUSIVE  # the first stands


async def test_latest_is_the_newest_cumulative_and_ignores_other_kinds(
    store: MongoParityReportStore,
) -> None:
    await store.append(report(last=date(2026, 3, 4)), "em185-test-1")
    await store.append(report(recorded_at=NOW + timedelta(days=1)), "em185-test-2")
    await store.append(
        report(ParityKind.DAILY, last=date(2026, 3, 2), recorded_at=NOW + timedelta(days=9)),
        "em185-test-3",
    )

    latest = await store.latest(STRATEGY, HASH)
    assert latest is not None and latest.last_session == date(2026, 3, 5)
    assert await store.latest(STRATEGY, "sha256:other") is None


async def test_dailies_come_back_oldest_first(store: MongoParityReportStore) -> None:
    for day in (4, 2, 3):
        d = date(2026, 3, day)
        await store.append(report(ParityKind.DAILY, d, d), f"em185-test-d{day}")

    assert [r.first_session.day for r in await store.dailies(STRATEGY, HASH)] == [2, 3, 4]
    assert [r.strategy for r in await store.daily_on(date(2026, 3, 3))].count(STRATEGY) == 1
