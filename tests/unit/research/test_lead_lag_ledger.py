"""EM-180: `InMemoryLeadLagTrialLedger`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from emporos.domain.experiments import TrialRole
from emporos.domain.lead_lag_trials import DuplicateLeadLagTrialError, LeadLagTrial
from emporos.research.lead_lag_ledger import InMemoryLeadLagTrialLedger

RECORDED = datetime(2026, 3, 1, tzinfo=UTC)


def trial(trial_id: str, recorded_at: datetime = RECORDED) -> LeadLagTrial:
    return LeadLagTrial(
        trial_id=trial_id, hypothesis_id="h1", predictor="market", expression="stock",
        subject="NSE:1", early_horizon_label="30m", target_horizon_label="15m", direction="up",
        role=TrialRole.STANDALONE, dataset_version="sha256:abc", cost_model="test+0bps",
        regime_axis=None, regime_label=None, sample_size=10, conditional_expectancy=None,
        cost_adjusted_expectancy=None, hit_rate=None, rank_ic=None, decile_spread=None,
        t_statistic=None, recorded_at=recorded_at,
    )  # fmt: skip


def test_a_trial_needs_an_id() -> None:
    with pytest.raises(ValueError, match="id"):
        trial("")


def test_expression_must_be_stock_or_sector() -> None:
    with pytest.raises(ValueError, match="expression"):
        LeadLagTrial(
            trial_id="t1", hypothesis_id="h1", predictor="market", expression="index",
            subject="NSE:1", early_horizon_label="30m", target_horizon_label="15m",
            direction="up", role=TrialRole.STANDALONE, dataset_version="sha256:abc",
            cost_model="test+0bps", regime_axis=None, regime_label=None, sample_size=10,
            conditional_expectancy=None, cost_adjusted_expectancy=None, hit_rate=None,
            rank_ic=None, decile_spread=None, t_statistic=None, recorded_at=RECORDED,
        )  # fmt: skip


def test_direction_must_be_up_or_down() -> None:
    with pytest.raises(ValueError, match="direction"):
        LeadLagTrial(
            trial_id="t1", hypothesis_id="h1", predictor="market", expression="stock",
            subject="NSE:1", early_horizon_label="30m", target_horizon_label="15m",
            direction="sideways", role=TrialRole.STANDALONE, dataset_version="sha256:abc",
            cost_model="test+0bps", regime_axis=None, regime_label=None, sample_size=10,
            conditional_expectancy=None, cost_adjusted_expectancy=None, hit_rate=None,
            rank_ic=None, decile_spread=None, t_statistic=None, recorded_at=RECORDED,
        )  # fmt: skip


async def test_the_ledger_refuses_to_append_the_same_id_twice() -> None:
    ledger = InMemoryLeadLagTrialLedger()
    await ledger.append(trial("t1"))

    with pytest.raises(DuplicateLeadLagTrialError):
        await ledger.append(trial("t1"))


async def test_all_returns_every_trial_oldest_first() -> None:
    ledger = InMemoryLeadLagTrialLedger()
    later = trial("t2", recorded_at=datetime(2026, 3, 2, tzinfo=UTC))
    earlier = trial("t1", recorded_at=datetime(2026, 3, 1, tzinfo=UTC))
    await ledger.append(later)
    await ledger.append(earlier)

    assert [t.trial_id for t in await ledger.all()] == ["t1", "t2"]
