import pytest

from emporos.persistence.collections import Collection
from emporos.persistence.errors import SchemaDriftError
from emporos.persistence.migrations import IndexInfo, MigrationRunner
from emporos.persistence.schema import PLATFORM_SCHEMA
from tests.support.fakes import InMemorySchemaStore


def _runner(store: InMemorySchemaStore) -> MigrationRunner:
    return MigrationRunner(store, PLATFORM_SCHEMA)


async def test_first_run_creates_every_collection_and_index() -> None:
    store = InMemorySchemaStore()
    runner = _runner(store)

    report = await runner.apply()

    expected_indexes = sum(len(spec.indexes) for spec in PLATFORM_SCHEMA.collections)
    assert len(report.created_collections) == len(PLATFORM_SCHEMA.collections)
    assert len(report.created_indexes) == expected_indexes
    assert await runner.missing_indexes() == ()


async def test_second_run_is_a_no_op() -> None:
    store = InMemorySchemaStore()
    runner = _runner(store)
    await runner.apply()

    report = await runner.apply()

    assert not report.changed
    assert report.summary() == "schema already up to date"


async def test_a_dropped_index_is_recreated() -> None:
    store = InMemorySchemaStore()
    runner = _runner(store)
    await runner.apply()
    store.drop_index(Collection.ORDERS, "ordertag_1")

    assert await runner.missing_indexes() == ("orders.ordertag_1",)
    report = await runner.apply()

    assert report.created_indexes == ("orders.ordertag_1",)
    assert report.summary() == "created 0 collection(s) and 1 index(es)"


async def test_missing_indexes_before_any_migration_lists_everything() -> None:
    runner = _runner(InMemorySchemaStore())

    missing = await runner.missing_indexes()

    assert "orders.idempotency_key_1" in missing
    assert "candles.instrument_id_1_timeframe_1_ts_1" in missing


async def test_an_index_that_lost_its_unique_flag_is_reported_as_drift() -> None:
    store = InMemorySchemaStore()
    runner = _runner(store)
    await runner.apply()
    store.replace_index(
        Collection.ORDERS,
        "idempotency_key_1",
        IndexInfo(keys=(("idempotency_key", 1),), unique=False, expire_after_seconds=None),
    )

    with pytest.raises(SchemaDriftError, match="orders.idempotency_key_1"):
        await runner.apply()


async def test_a_changed_ttl_is_reported_as_drift() -> None:
    store = InMemorySchemaStore()
    runner = _runner(store)
    await runner.apply()
    store.replace_index(
        Collection.SYSTEM_EVENTS,
        "ts_1",
        IndexInfo(keys=(("ts", 1),), unique=False, expire_after_seconds=1),
    )

    with pytest.raises(SchemaDriftError):
        await runner.apply()
