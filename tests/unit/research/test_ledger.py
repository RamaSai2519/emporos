"""EM-178: `InMemoryFeatureTrialLedger`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from emporos.domain.experiments import TrialRole
from emporos.domain.feature_trials import DuplicateFeatureTrialError, FeatureTrial
from emporos.research.ledger import InMemoryFeatureTrialLedger

RECORDED = datetime(2026, 3, 1, tzinfo=UTC)


def trial(trial_id: str, recorded_at: datetime = RECORDED) -> FeatureTrial:
    return FeatureTrial(
        trial_id=trial_id, hypothesis_id="h1", feature_name="momentum", feature_version="v1",
        horizon_label="5m", role=TrialRole.STANDALONE, dataset_version="sha256:abc",
        cost_model="test+0bps", regime_axis=None, regime_label=None, sample_size=10,
        conditional_expectancy=None, cost_adjusted_expectancy=None, hit_rate=None,
        rank_ic=None, decile_spread=None, t_statistic=None, recorded_at=recorded_at,
    )  # fmt: skip


def test_a_trial_needs_an_id() -> None:
    with pytest.raises(ValueError, match="id"):
        trial("")


def test_recorded_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        trial("t1", recorded_at=datetime(2026, 3, 1))


async def test_the_ledger_refuses_to_append_the_same_id_twice() -> None:
    ledger = InMemoryFeatureTrialLedger()
    await ledger.append(trial("t1"))

    with pytest.raises(DuplicateFeatureTrialError):
        await ledger.append(trial("t1"))


async def test_all_returns_every_trial_oldest_first() -> None:
    ledger = InMemoryFeatureTrialLedger()
    later = trial("t2", recorded_at=datetime(2026, 3, 2, tzinfo=UTC))
    earlier = trial("t1", recorded_at=datetime(2026, 3, 1, tzinfo=UTC))
    await ledger.append(later)
    await ledger.append(earlier)

    assert [t.trial_id for t in await ledger.all()] == ["t1", "t2"]
