"""Instrument history and atomic replacement against real Atlas (EM-27).

Uses throwaway collections (never the real `instruments`), dropped afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from emporos.domain.instruments import Instrument
from emporos.instruments.differ import InstrumentDiff, InstrumentDiffer
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from tests.support.instrument_rows import instrument
from tests.support.mongo_instruments import Rig

pytestmark = pytest.mark.integration

T0 = datetime(2026, 3, 2, 3, 30, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


async def _sync(rig: Rig, new: list[Instrument], at: datetime) -> None:
    diff = InstrumentDiffer().diff(await rig.store.load_current(), new)
    await rig.store.apply(diff, at)


async def _collections(rig: Rig) -> set[str]:
    return set(await rig.database.list_collection_names())


async def test_the_first_sync_bulk_loads_the_master_without_any_history(rig: Rig) -> None:
    master = [instrument(str(n)) for n in range(1, 6)]

    await _sync(rig, master, T0)

    assert sorted(await rig.store.load_current(), key=lambda i: i.token) == master
    assert {r.valid_from for r in await rig.instruments.all()} == {T0}
    assert await rig.versions.count() == 0
    assert rig.staging_name not in await _collections(rig)


async def test_the_bulk_load_is_complete_for_a_realistic_size(rig: Rig) -> None:
    await _sync(rig, [instrument(str(n)) for n in range(2500)], T0)

    assert await rig.instruments.count() == 2500


async def test_the_bulk_load_leaves_the_same_indexes_a_migration_declares(rig: Rig) -> None:
    await _sync(rig, [instrument("1")], T0)

    runner = MigrationRunner(MongoSchemaStore(rig.database), rig.schema)

    assert await runner.missing_indexes() == ()
    assert not (await runner.apply()).changed  # no drift: unique flag and keys identical


async def test_a_second_identical_sync_writes_nothing(rig: Rig) -> None:
    master = [instrument("1"), instrument("2")]
    await _sync(rig, master, T0)

    await _sync(rig, master, T1)

    assert {r.valid_from for r in await rig.instruments.all()} == {T0}
    assert await rig.versions.count() == 0


async def test_a_changed_field_appends_the_old_definition_as_a_closed_version(rig: Rig) -> None:
    await _sync(rig, [instrument("1"), instrument("2")], T0)

    await _sync(rig, [instrument("1", tradingsymbol="RENAMED-EQ"), instrument("2")], T1)

    (old,) = await rig.versions.for_instrument("NSE:1")
    assert (old.tradingsymbol, old.valid_from, old.valid_to) == ("SYM1-EQ", T0, T1)
    current = {r.token: r for r in await rig.instruments.all()}
    assert (current["1"].tradingsymbol, current["1"].valid_from) == ("RENAMED-EQ", T1)
    assert current["2"].valid_from == T0  # untouched
    assert await rig.versions.for_instrument("NSE:2") == []


async def test_repeated_changes_build_a_contiguous_history(rig: Rig) -> None:
    await _sync(rig, [instrument("1", lot_size=1)], T0)
    await _sync(rig, [instrument("1", lot_size=2)], T1)
    await _sync(rig, [instrument("1", lot_size=3)], T1 + timedelta(days=1))

    history = await rig.versions.for_instrument("NSE:1")

    assert [(v.lot_size, v.valid_from, v.valid_to) for v in history] == [
        (1, T0, T1),
        (2, T1, T1 + timedelta(days=1)),
    ]


async def test_a_removed_instrument_leaves_the_master_and_keeps_its_history(rig: Rig) -> None:
    await _sync(rig, [instrument("1"), instrument("2")], T0)

    await _sync(rig, [instrument("1")], T1)

    assert [i.token for i in await rig.store.load_current()] == ["1"]
    (history,) = await rig.versions.for_instrument("NSE:2")
    assert (history.valid_from, history.valid_to) == (T0, T1)


async def test_an_incremental_diff_larger_than_one_batch_is_applied_in_one_transaction(
    rig: Rig,
) -> None:
    await _sync(rig, [instrument("0")], T0)

    await _sync(rig, [instrument(str(n)) for n in range(1500)], T1)

    assert await rig.instruments.count() == 1500


async def test_a_failure_part_way_through_an_incremental_swap_leaves_nothing_half_applied(
    rig: Rig,
) -> None:
    await _sync(rig, [instrument("1"), instrument("2")], T0)
    before = sorted(await rig.store.load_current(), key=lambda i: i.token)
    # Instrument 1 changes and 2 is removed (both succeed), then adding a *duplicate* of 1
    # collides on the unique (exchange, token) index — the crash lands after several writes.
    diff = InstrumentDiffer().diff(before, [instrument("1", name="Changed")])
    poisoned = InstrumentDiff(
        added=(instrument("1", name="Collides"),), changed=diff.changed, removed=diff.removed
    )

    with pytest.raises(DuplicateRecordError):
        await rig.store.apply(poisoned, T1)

    assert sorted(await rig.store.load_current(), key=lambda i: i.token) == before
    assert await rig.versions.count() == 0
    assert {r.valid_from for r in await rig.instruments.all()} == {T0}


async def test_a_failure_during_the_bulk_load_leaves_the_master_untouched_and_cleans_up(
    rig: Rig,
) -> None:
    duplicated = InstrumentDiff(added=(instrument("1"), instrument("2"), instrument("1")))

    with pytest.raises(DuplicateRecordError):
        await rig.store.apply(duplicated, T0)

    assert await rig.instruments.count() == 0
    assert rig.staging_name not in await _collections(rig)


async def test_a_staging_collection_left_by_a_crashed_run_is_discarded(rig: Rig) -> None:
    await rig.database[rig.staging_name].insert_one({"_id": "junk", "stale": True})

    await _sync(rig, [instrument("1")], T0)

    assert [i.token for i in await rig.store.load_current()] == ["1"]
    assert rig.staging_name not in await _collections(rig)


async def test_applying_an_empty_diff_is_a_no_op(rig: Rig) -> None:
    await rig.store.apply(InstrumentDiffer().diff([], []), T0)

    assert await rig.instruments.count() == 0
