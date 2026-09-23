"""EM-179: `InMemoryCrossSectionalTrialLedger`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from emporos.domain.cross_sectional_trials import (
    CrossSectionalTrial,
    DuplicateCrossSectionalTrialError,
)
from emporos.domain.experiments import TrialRole
from emporos.research.cross_sectional_ledger import InMemoryCrossSectionalTrialLedger

RECORDED = datetime(2026, 3, 1, tzinfo=UTC)


def trial(trial_id: str, recorded_at: datetime = RECORDED) -> CrossSectionalTrial:
    return CrossSectionalTrial(
        trial_id=trial_id, hypothesis_id="h1", signal_horizon_label="30m",
        holding_horizon_label="15m", tail="top", role=TrialRole.STANDALONE,
        dataset_version="sha256:abc", cost_model="test+0bps", regime_axis=None,
        regime_label=None, sample_size=10, gross_expectancy=None, net_expectancy=None,
        hit_rate=None, t_statistic=None, recorded_at=recorded_at,
    )  # fmt: skip


def test_a_trial_needs_an_id() -> None:
    with pytest.raises(ValueError, match="id"):
        trial("")


def test_tail_must_be_top_or_bottom() -> None:
    with pytest.raises(ValueError, match="tail"):
        CrossSectionalTrial(
            trial_id="t1", hypothesis_id="h1", signal_horizon_label="30m",
            holding_horizon_label="15m", tail="sideways", role=TrialRole.STANDALONE,
            dataset_version="sha256:abc", cost_model="test+0bps", regime_axis=None,
            regime_label=None, sample_size=10, gross_expectancy=None, net_expectancy=None,
            hit_rate=None, t_statistic=None, recorded_at=RECORDED,
        )  # fmt: skip


def test_recorded_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        trial("t1", recorded_at=datetime(2026, 3, 1))


async def test_the_ledger_refuses_to_append_the_same_id_twice() -> None:
    ledger = InMemoryCrossSectionalTrialLedger()
    await ledger.append(trial("t1"))

    with pytest.raises(DuplicateCrossSectionalTrialError):
        await ledger.append(trial("t1"))


async def test_all_returns_every_trial_oldest_first() -> None:
    ledger = InMemoryCrossSectionalTrialLedger()
    later = trial("t2", recorded_at=datetime(2026, 3, 2, tzinfo=UTC))
    earlier = trial("t1", recorded_at=datetime(2026, 3, 1, tzinfo=UTC))
    await ledger.append(later)
    await ledger.append(earlier)

    assert [t.trial_id for t in await ledger.all()] == ["t1", "t2"]
