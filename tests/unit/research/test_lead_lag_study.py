"""EM-180: `LeadLagStudy` — wiring the engine, the ledger and the holdout gate together."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from tests.unit.research.conftest import bar

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle
from emporos.domain.experiments import TrialRole
from emporos.domain.fees import FeeSchedule
from emporos.domain.hypotheses import HypothesisDeclaration
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.sizing import DeclaredSize
from emporos.research.costs import TransactionCostModel
from emporos.research.horizons import Horizon
from emporos.research.hypotheses import HoldoutViolation
from emporos.research.lead_lag import LeadLagEngine
from emporos.research.lead_lag_ledger import InMemoryLeadLagTrialLedger
from emporos.research.lead_lag_study import LeadLagStudy

SCHEDULE = FeeSchedule(
    name="test",
    effective_from=date(2026, 1, 1),
    brokerage_flat=Money.of("20"),
    brokerage_percent=Decimal("0.1"),
    brokerage_minimum=Money.of("5"),
    stt_sell_percent=Decimal("0.025"),
    exchange_transaction_percent={Exchange.NSE: Decimal("0.0030699")},
    sebi_per_crore=Money.of("10"),
    stamp_duty_buy_percent=Decimal("0.003"),
    gst_percent=Decimal("18"),
)
NOW = datetime(2026, 3, 1, tzinfo=UTC)
EARLY = Horizon(timedelta(minutes=10), bars=2)
TARGET = Horizon(timedelta(minutes=10), bars=2)
DAY1, DAY2 = date(2026, 1, 5), date(2026, 1, 6)


def _bars() -> list[Candle]:
    return [bar(i, 100 + i) for i in range(4)]


def series() -> (
    tuple[dict[date, list[Decimal]], dict[date, list[Decimal]], dict[date, list[Candle]]]
):
    predictor_by_day = {DAY1: [Decimal("0.01"), Decimal("0.01")], DAY2: [Decimal("0.02")] * 2}
    target_by_day = {
        DAY1: [Decimal("0.005")] * 2 + [Decimal("0.02")] * 2,
        DAY2: [Decimal("0.01")] * 2 + [Decimal("0.03")] * 2,
    }
    regime_bars_by_day = {DAY1: _bars(), DAY2: _bars()}
    return predictor_by_day, target_by_day, regime_bars_by_day


def study() -> tuple[LeadLagStudy, InMemoryLeadLagTrialLedger]:
    engine = LeadLagEngine(
        early_horizons=[EARLY], target_horizons=[TARGET],
        cost_model=TransactionCostModel(SCHEDULE), exchange=Exchange.NSE,
        size=DeclaredSize(Decimal(50_000)),
    )  # fmt: skip
    ledger = InMemoryLeadLagTrialLedger()
    return LeadLagStudy(engine, ledger, FixedClock(NOW)), ledger


def hypothesis(**overrides: object) -> HypothesisDeclaration:
    fields: dict[str, object] = {
        "hypothesis_id": "h1", "feature_name": "lead_lag", "feature_version": "v1",
        "study_first": date(2026, 1, 1), "study_last": date(2026, 2, 1),
        "holdout_first": date(2026, 1, 20), "holdout_last": date(2026, 2, 1),
        "declared_at": NOW,
    }  # fmt: skip
    fields.update(overrides)
    return HypothesisDeclaration(**fields)  # type: ignore[arg-type]


async def test_a_standalone_run_outside_the_holdout_records_trials() -> None:
    lead_lag_study, ledger = study()
    predictor_by_day, target_by_day, regime_bars_by_day = series()

    trials = await lead_lag_study.run(
        predictor_by_day, target_by_day, regime_bars_by_day, None,
        predictor="market", expression="stock", subject="NSE:1",
        hypothesis=hypothesis(), role=TrialRole.TRAIN, dataset_version="sha256:x",
        study_first=date(2026, 1, 1), study_last=date(2026, 1, 15),
    )  # fmt: skip

    assert trials
    assert sorted(await ledger.all(), key=lambda t: t.trial_id) == sorted(
        trials, key=lambda t: t.trial_id
    )


async def test_a_train_run_touching_the_holdout_is_refused() -> None:
    lead_lag_study, _ = study()
    predictor_by_day, target_by_day, regime_bars_by_day = series()

    with pytest.raises(HoldoutViolation):
        await lead_lag_study.run(
            predictor_by_day, target_by_day, regime_bars_by_day, None,
            predictor="market", expression="stock", subject="NSE:1",
            hypothesis=hypothesis(), role=TrialRole.TRAIN, dataset_version="sha256:x",
            study_first=date(2026, 1, 1), study_last=date(2026, 1, 25),
        )  # fmt: skip


async def test_a_test_run_over_exactly_the_holdout_records_trials() -> None:
    lead_lag_study, ledger = study()
    predictor_by_day, target_by_day, regime_bars_by_day = series()

    trials = await lead_lag_study.run(
        predictor_by_day, target_by_day, regime_bars_by_day, None,
        predictor="market", expression="stock", subject="NSE:1",
        hypothesis=hypothesis(), role=TrialRole.TEST, dataset_version="sha256:x",
        study_first=date(2026, 1, 20), study_last=date(2026, 2, 1),
    )  # fmt: skip

    assert trials
    assert all(t.role is TrialRole.TEST for t in trials)
    assert sorted(await ledger.all(), key=lambda t: t.trial_id) == sorted(
        trials, key=lambda t: t.trial_id
    )
