"""`MongoFeatureTrialLedger` on real Atlas (EM-178). `h1|em178-test` is a made-up trial id that
never appears in real research, so the shared dev database is never polluted for real runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.experiments import TrialRole
from emporos.domain.feature_trials import DuplicateFeatureTrialError, FeatureTrial
from emporos.persistence.collections import Collection
from emporos.persistence.feature_ledger import MongoFeatureTrialLedger
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA

pytestmark = pytest.mark.integration

TRIAL_ID = "em178-test|h1|NSE:TESTQ99|5m|pooled|all"
RECORDED = datetime(2026, 3, 5, tzinfo=UTC)


def trial(trial_id: str = TRIAL_ID) -> FeatureTrial:
    return FeatureTrial(
        trial_id=trial_id, hypothesis_id="em178-test-h1", feature_name="momentum",
        feature_version="v1", horizon_label="5m", role=TrialRole.STANDALONE,
        dataset_version="local-parquet-cache", cost_model="test+0bps", regime_axis=None,
        regime_label=None, sample_size=10, conditional_expectancy=Decimal("0.01"),
        cost_adjusted_expectancy=Decimal("0.005"), hit_rate=Decimal("0.6"),
        rank_ic=Decimal("0.2"), decile_spread=Decimal("0.03"), t_statistic=Decimal("1.5"),
        recorded_at=RECORDED,
    )  # fmt: skip


@pytest.fixture
async def ledger(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[MongoFeatureTrialLedger]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    try:
        yield MongoFeatureTrialLedger(database)
    finally:
        await database[Collection.FEATURE_TRIAL_LEDGER].delete_many({"_id": TRIAL_ID})


async def test_a_trial_round_trips(ledger: MongoFeatureTrialLedger) -> None:
    await ledger.append(trial())

    all_trials = await ledger.all()
    (mine,) = (t for t in all_trials if t.trial_id == TRIAL_ID)
    assert mine == trial()


async def test_appending_the_same_id_twice_is_refused_not_overwritten(
    ledger: MongoFeatureTrialLedger,
) -> None:
    await ledger.append(trial())

    with pytest.raises(DuplicateFeatureTrialError):
        await ledger.append(trial())
