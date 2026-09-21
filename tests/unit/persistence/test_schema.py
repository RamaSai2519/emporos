from datetime import timedelta

import pytest

from emporos.persistence.collections import Collection
from emporos.persistence.schema import (
    PLATFORM_SCHEMA,
    CollectionSpec,
    IndexSpec,
    Schema,
)


def test_every_collection_in_the_catalogue_is_declared_exactly_once() -> None:
    declared = [spec.name for spec in PLATFORM_SCHEMA.collections]

    assert sorted(declared) == sorted(Collection)


@pytest.mark.parametrize(
    ("collection", "fields"),
    [
        (Collection.ORDERS, ("idempotency_key",)),
        (Collection.ORDERS, ("ordertag",)),
        (Collection.EXECUTIONS, ("broker_trade_id",)),
        (Collection.CANDLES, ("instrument_id", "timeframe", "ts")),
        (Collection.COMMANDS, ("idempotency_key",)),
        (Collection.INSTRUMENTS, ("exchange", "token")),
        (Collection.POSITIONS, ("account_id", "instrument_id")),
        (Collection.ACCOUNTS, ("client_code",)),
        (Collection.STRATEGIES, ("name",)),
        (Collection.MARKET_CALENDAR, ("date",)),
    ],
)
def test_the_unique_indexes_that_guarantee_idempotency_are_declared(
    collection: Collection, fields: tuple[str, ...]
) -> None:
    unique = {
        tuple(name for name, _ in index.keys)
        for index in PLATFORM_SCHEMA.spec_for(collection).indexes
        if index.unique
    }

    assert fields in unique


def test_no_collection_holds_raw_ticks() -> None:
    """Nothing ever wrote to the optional tick archive, so it is gone: raw ticks are not kept."""
    assert "ticks" not in {collection.value for collection in Collection}


def test_system_events_expire_after_thirty_days() -> None:
    ttl = [
        index.expire_after
        for index in PLATFORM_SCHEMA.spec_for(Collection.SYSTEM_EVENTS).indexes
        if index.expire_after
    ]

    assert ttl == [timedelta(days=30)]


def test_index_name_matches_the_mongo_default_naming() -> None:
    assert IndexSpec.on("a", "b").name == "a_1_b_1"


def test_ttl_index_reports_whole_seconds() -> None:
    assert IndexSpec.on("ts", expire_after=timedelta(minutes=2)).expire_after_seconds == 120
    assert IndexSpec.on("ts").expire_after_seconds is None


def test_an_index_needs_keys() -> None:
    with pytest.raises(ValueError, match="at least one key"):
        IndexSpec(keys=())


def test_a_ttl_index_must_be_on_one_field() -> None:
    with pytest.raises(ValueError, match="exactly one field"):
        IndexSpec.on("a", "b", expire_after=timedelta(days=1))


def test_a_collection_cannot_declare_the_same_index_twice() -> None:
    with pytest.raises(ValueError, match="duplicate index"):
        CollectionSpec(Collection.ORDERS, (IndexSpec.on("a"), IndexSpec.on("a")))


def test_a_schema_cannot_declare_a_collection_twice() -> None:
    with pytest.raises(ValueError, match="declared twice"):
        Schema((CollectionSpec(Collection.ORDERS), CollectionSpec(Collection.ORDERS)))


def test_spec_for_an_undeclared_collection_raises() -> None:
    with pytest.raises(KeyError):
        Schema(()).spec_for(Collection.ORDERS)
