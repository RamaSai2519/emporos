"""`MongoCrossSectionalTrialLedger` on real Atlas (EM-179). `h1|standalone|...` is a made-up trial
id that never appears in real research, so the shared dev database is never polluted for real
runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.cross_sectional_trials import (
    CrossSectionalTrial,
    DuplicateCrossSectionalTrialError,
)
from emporos.domain.experiments import TrialRole
from emporos.persistence.collections import Collection
from emporos.persistence.cross_sectional_ledger import MongoCrossSectionalTrialLedger
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA

pytestmark = pytest.mark.integration

TRIAL_ID = "em179-test-h1|standalone|top|30m|15m|pooled|all"
RECORDED = datetime(2026, 3, 5, tzinfo=UTC)


def trial(trial_id: str = TRIAL_ID) -> CrossSectionalTrial:
    return CrossSectionalTrial(
        trial_id=trial_id, hypothesis_id="em179-test-h1", signal_horizon_label="30m",
        holding_horizon_label="15m", tail="top", role=TrialRole.STANDALONE,
        dataset_version="local-parquet-cache", cost_model="test+0bps", regime_axis=None,
        regime_label=None, sample_size=10, gross_expectancy=Decimal("0.01"),
        net_expectancy=Decimal("0.005"), hit_rate=Decimal("0.6"), t_statistic=Decimal("1.5"),
        recorded_at=RECORDED,
    )  # fmt: skip


@pytest.fixture
async def ledger(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[MongoCrossSectionalTrialLedger]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    try:
        yield MongoCrossSectionalTrialLedger(database)
    finally:
        await database[Collection.CROSS_SECTIONAL_TRIAL_LEDGER].delete_many({"_id": TRIAL_ID})


async def test_a_trial_round_trips(ledger: MongoCrossSectionalTrialLedger) -> None:
    await ledger.append(trial())

    all_trials = await ledger.all()
    (mine,) = (t for t in all_trials if t.trial_id == TRIAL_ID)
    assert mine == trial()


async def test_appending_the_same_id_twice_is_refused_not_overwritten(
    ledger: MongoCrossSectionalTrialLedger,
) -> None:
    await ledger.append(trial())

    with pytest.raises(DuplicateCrossSectionalTrialError):
        await ledger.append(trial())
