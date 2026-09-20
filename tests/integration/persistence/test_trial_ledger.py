"""EM-117: the trial ledger on real Atlas: exact decimals survive, a trial id is never
overwritten, and the ledger offers no edit or delete. Uses a random scratch collection (the dev
database is shared) and drops it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from bson.decimal128 import Decimal128
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.domain.experiments import DuplicateTrialError, Trial, TrialRole, Verdict
from emporos.persistence.trial_ledger import MongoTrialLedger

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def trial(tag: str, minute: int = 0, **overrides: object) -> Trial:
    values: dict[str, object] = {
        "trial_id": tag, "experiment": "it", "strategy": "orb_v1", "candidate": "r3_t15_s10",
        "role": TrialRole.TEST, "dataset_version": "d1", "config_hash": "abc",
        "cost_model": "fees-v1", "recorded_at": T0 + timedelta(minutes=minute),
    }  # fmt: skip
    values.update(overrides)
    return Trial(**values)  # type: ignore[arg-type]


@pytest.fixture
async def scratch(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[tuple[MongoTrialLedger, str, AsyncDatabase[Mapping[str, Any]]]]:
    name = "zz_trials_" + IdGenerator().new_ulid()
    try:
        yield MongoTrialLedger(database, name), name, database
    finally:
        await database.drop_collection(name)


async def test_a_full_trial_round_trips_with_exact_decimals(scratch: Any) -> None:
    ledger, name, database = scratch
    full = trial(
        "full", run_id="run-1", trade_count=40, net_pnl=Decimal("-3769.43"),
        daily_sharpe=Decimal("-0.1234567890123456789012345678"), verdict=Verdict.REJECTED,
        note="no edge",
    )  # fmt: skip

    await ledger.append(full)

    assert await ledger.all() == [full]
    stored = await database[name].find_one({"_id": "full"})
    assert isinstance(stored["daily_sharpe"], Decimal128)  # never a float
    assert isinstance(stored["net_pnl"], Decimal128)


async def test_a_trial_with_unknowns_keeps_them_unknown(scratch: Any) -> None:
    ledger, _, _ = scratch
    thin = trial("thin", config_hash=None)

    await ledger.append(thin)

    (back,) = await ledger.all()
    assert (back.net_pnl, back.daily_sharpe, back.trade_count, back.verdict) == (None,) * 4
    assert back.config_hash is None


async def test_an_id_is_refused_the_second_time_and_the_first_survives(scratch: Any) -> None:
    ledger, _, _ = scratch
    await ledger.append(trial("dup", net_pnl=Decimal("1")))

    with pytest.raises(DuplicateTrialError):
        await ledger.append(trial("dup", net_pnl=Decimal("999")))

    assert [t.net_pnl for t in await ledger.all()] == [Decimal("1")]


async def test_lists_oldest_first(scratch: Any) -> None:
    ledger, _, _ = scratch
    for tag, minute in (("c", 3), ("a", 1), ("b", 2)):
        await ledger.append(trial(tag, minute))

    assert [t.trial_id for t in await ledger.all()] == ["a", "b", "c"]


def test_the_ledger_has_no_way_to_edit_or_remove() -> None:
    public = {name for name in dir(MongoTrialLedger) if not name.startswith("_")}

    assert public == {"append", "all"}
