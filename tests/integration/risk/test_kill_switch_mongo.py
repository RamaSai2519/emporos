"""The Mongo half of the kill switch on real Atlas (EM-74), in a scratch collection so the real
`kill_switch` document of the shared dev database is never touched."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.clock import AsyncioSleeper, FixedClock, SystemClock
from emporos.core.ids import IdGenerator
from emporos.persistence.repositories import KillSwitchRepository
from emporos.risk.kill_switch import (
    FileSentinelKillSwitch,
    KillSwitchControl,
    KillSwitchMonitor,
    MongoKillSwitch,
)
from tests.support.risk import NOW

pytestmark = pytest.mark.integration

Database = AsyncDatabase[Mapping[str, Any]]


@pytest.fixture
async def flag(database: Database) -> AsyncIterator[MongoKillSwitch]:
    name = f"zz_kill_switch_{IdGenerator().new_ulid().lower()}"
    try:
        yield MongoKillSwitch(KillSwitchRepository(database, name))
    finally:
        await database.drop_collection(name)


async def test_a_missing_document_reads_as_not_halted(flag: MongoKillSwitch) -> None:
    assert (await flag.read()).halted is False


async def test_engaging_and_releasing_round_trip_through_atlas(flag: MongoKillSwitch) -> None:
    await flag.engage("integration drill", "rama", NOW)
    engaged = await flag.read()
    assert engaged.halted and engaged.reason == "integration drill"

    await flag.release("rama", NOW)
    assert (await flag.read()).halted is False


async def test_engaging_twice_keeps_a_single_document(
    flag: MongoKillSwitch, database: Database
) -> None:
    await flag.engage("first", "rama", NOW)
    await flag.engage("second", "rama", NOW)
    assert (await flag.read()).reason == "second"


async def test_the_audit_fields_record_who_and_when(flag: MongoKillSwitch) -> None:
    repository = flag._repository
    await flag.engage("why", "rama", NOW)
    record = await repository.current()
    assert record is not None
    assert (record.halted, record.set_by, record.changed_at, record.reason) == (
        True,
        "rama",
        NOW,
        "why",
    )


async def test_the_monitor_sees_a_mongo_halt_and_a_sentinel_halt_alike(
    flag: MongoKillSwitch, tmp_path: Path
) -> None:
    sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
    monitor = KillSwitchMonitor([flag, sentinel], SystemClock(), AsyncioSleeper())

    assert (await monitor.refresh()).halted is False

    await flag.engage("via mongo", "rama", NOW)
    via_mongo = await monitor.refresh()
    assert (via_mongo.halted, via_mongo.known, via_mongo.source) == (True, True, "mongo")

    await flag.release("rama", NOW)
    await sentinel.engage("via file", "rama", NOW)
    via_file = await monitor.refresh()
    assert (via_file.halted, via_file.source) == (True, "file")


async def test_the_control_sets_and_clears_both_places_on_real_atlas(
    flag: MongoKillSwitch, tmp_path: Path
) -> None:
    sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
    control = KillSwitchControl([flag, sentinel], [flag, sentinel], FixedClock(NOW))

    assert all(o.ok for o in await control.engage("drill", "rama"))
    assert [(o.name, o.halted) for o in await control.status()] == [("mongo", True), ("file", True)]

    assert all(o.ok for o in await control.release("rama"))
    assert [(o.name, o.halted) for o in await control.status()] == [
        ("mongo", False),
        ("file", False),
    ]
