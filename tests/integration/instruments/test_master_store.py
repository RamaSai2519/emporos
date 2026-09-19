"""The instrument versioning + atomic swap against real Atlas transactions (EM-27).

Uses throwaway collections (never the real `instruments`), dropped afterwards.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.instruments.differ import InstrumentDiffer
from emporos.instruments.store import MongoInstrumentMasterStore
from emporos.persistence.collections import Collection
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.repositories import InstrumentRepository, InstrumentVersionRepository
from emporos.persistence.schema import PLATFORM_SCHEMA, CollectionSpec, Schema
from emporos.persistence.transactions import TransactionRunner
from tests.support.instrument_rows import instrument

pytestmark = pytest.mark.integration

T0 = datetime(2026, 3, 2, 3, 30, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


@dataclass
class Rig:
    store: MongoInstrumentMasterStore
    instruments: InstrumentRepository
    versions: InstrumentVersionRepository


@pytest.fixture
async def rig(dev_settings: Any) -> AsyncIterator[Rig]:
    mongo = MongoClientFactory(dev_settings)
    database: AsyncDatabase[Mapping[str, Any]] = mongo.database()
    suffix = IdGenerator().new_ulid().lower()
    instruments_name, versions_name = f"zz_instruments_{suffix}", f"zz_versions_{suffix}"
    schema = Schema(
        (
            CollectionSpec(
                instruments_name, PLATFORM_SCHEMA.spec_for(Collection.INSTRUMENTS).indexes
            ),
            CollectionSpec(
                versions_name, PLATFORM_SCHEMA.spec_for(Collection.INSTRUMENT_VERSIONS).indexes
            ),
        )
    )
    await MigrationRunner(MongoSchemaStore(database), schema).apply()
    instruments = InstrumentRepository(database, instruments_name)
    versions = InstrumentVersionRepository(database, versions_name)
    try:
        yield Rig(
            MongoInstrumentMasterStore(TransactionRunner(mongo.client), instruments, versions),
            instruments,
            versions,
        )
    finally:
        await database[instruments_name].drop()
        await database[versions_name].drop()
        await mongo.close()


async def _sync(rig: Rig, new: list[Any], at: datetime) -> None:
    diff = InstrumentDiffer().diff(await rig.store.load_current(), new)
    await rig.store.apply(diff, at)


async def test_a_first_sync_loads_every_instrument_with_an_open_version(rig: Rig) -> None:
    master = [instrument(str(n)) for n in range(1, 6)]

    await _sync(rig, master, T0)

    assert sorted(await rig.store.load_current(), key=lambda i: i.token) == master
    versions = await rig.versions.find({})
    assert len(versions) == 5
    assert {v.valid_from for v in versions} == {T0} and {v.valid_to for v in versions} == {None}


async def test_a_sync_larger_than_one_batch_is_applied_completely(rig: Rig) -> None:
    master = [instrument(str(n)) for n in range(2500)]

    await _sync(rig, master, T0)

    assert await rig.instruments.count() == 2500
    assert await rig.versions.count() == 2500


async def test_a_second_identical_sync_writes_nothing(rig: Rig) -> None:
    master = [instrument("1"), instrument("2")]
    await _sync(rig, master, T0)

    await _sync(rig, master, T1)

    assert await rig.versions.count() == 2
    assert {v.valid_from for v in await rig.versions.find({})} == {T0}


async def test_a_changed_field_closes_the_old_version_and_opens_a_new_one(rig: Rig) -> None:
    await _sync(rig, [instrument("1"), instrument("2")], T0)

    await _sync(rig, [instrument("1", tradingsymbol="RENAMED-EQ"), instrument("2")], T1)

    history = await rig.versions.for_instrument("NSE:1")
    assert [(v.tradingsymbol, v.valid_from, v.valid_to) for v in history] == [
        ("SYM1-EQ", T0, T1),
        ("RENAMED-EQ", T1, None),
    ]
    current = {i.token: i for i in await rig.store.load_current()}
    assert current["1"].tradingsymbol == "RENAMED-EQ"
    assert len(await rig.versions.for_instrument("NSE:2")) == 1  # untouched


async def test_a_removed_instrument_leaves_the_master_but_keeps_its_closed_history(
    rig: Rig,
) -> None:
    await _sync(rig, [instrument("1"), instrument("2")], T0)

    await _sync(rig, [instrument("1")], T1)

    assert [i.token for i in await rig.store.load_current()] == ["1"]
    (history,) = await rig.versions.for_instrument("NSE:2")
    assert history.valid_to == T1


async def test_a_failure_part_way_through_the_swap_leaves_nothing_half_applied(rig: Rig) -> None:
    await _sync(rig, [instrument("1"), instrument("2")], T0)
    before = sorted(await rig.store.load_current(), key=lambda i: i.token)
    # Change #1 and drop #2 succeed, then adding a *duplicate* of #1 collides on the unique
    # (exchange, token) index — the crash lands after several writes already happened.
    diff = InstrumentDiffer().diff(before, [instrument("1", name="Changed")])
    poisoned = type(diff)(
        added=(instrument("1", name="Collides"),), changed=diff.changed, removed=diff.removed
    )

    with pytest.raises(DuplicateRecordError):
        await rig.store.apply(poisoned, T1)

    assert sorted(await rig.store.load_current(), key=lambda i: i.token) == before
    versions = await rig.versions.find({})
    assert len(versions) == 2
    assert {v.valid_to for v in versions} == {None}


async def test_applying_an_empty_diff_is_a_no_op(rig: Rig) -> None:
    await rig.store.apply(InstrumentDiffer().diff([], []), T0)

    assert await rig.instruments.count() == 0
