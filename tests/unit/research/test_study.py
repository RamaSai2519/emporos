"""EM-178: `FeatureStudy` — wiring the engine, the ledger and the holdout gate together."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tests.unit.research.conftest import bar

from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.experiments import TrialRole
from emporos.domain.fees import FeeSchedule
from emporos.domain.hypotheses import HypothesisDeclaration
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.sizing import DeclaredSize
from emporos.research.costs import TransactionCostModel
from emporos.research.engine import AlphaDiscoveryEngine
from emporos.research.features import CausalHistory, FeatureDefinition
from emporos.research.horizons import ForwardReturnCalculator
from emporos.research.hypotheses import HoldoutViolation
from emporos.research.ledger import InMemoryFeatureTrialLedger
from emporos.research.study import FeatureStudy

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


class AlwaysOn:
    def compute(self, history: CausalHistory) -> Decimal | None:
        return Decimal(1)


def uptrend(n: int) -> list[Candle]:
    return [bar(i, 100 + i, instrument_id="NSE:1") for i in range(n)]


def study() -> tuple[FeatureStudy, InMemoryFeatureTrialLedger]:
    engine = AlphaDiscoveryEngine(
        ForwardReturnCalculator(Timeframe.M5),
        TransactionCostModel(SCHEDULE),
        Exchange.NSE,
        DeclaredSize(Decimal(10_000)),
    )
    ledger = InMemoryFeatureTrialLedger()
    return FeatureStudy(engine, ledger, FixedClock(NOW)), ledger


def hypothesis(**overrides: object) -> HypothesisDeclaration:
    fields: dict[str, object] = {
        "hypothesis_id": "h1", "feature_name": "momentum", "feature_version": "v1",
        "study_first": date(2026, 1, 1), "study_last": date(2026, 2, 1),
        "holdout_first": date(2026, 1, 20), "holdout_last": date(2026, 2, 1),
        "declared_at": NOW,
    }  # fmt: skip
    fields.update(overrides)
    return HypothesisDeclaration(**fields)  # type: ignore[arg-type]


async def test_a_standalone_run_outside_the_holdout_records_trials() -> None:
    feature_study, ledger = study()
    definition = FeatureDefinition("momentum", "v1", "", {})

    trials = await feature_study.run(
        definition, AlwaysOn(), {"NSE:1": uptrend(20)},
        hypothesis=hypothesis(), role=TrialRole.TRAIN,
        dataset_version="sha256:x", study_first=date(2026, 1, 1), study_last=date(2026, 1, 15),
    )  # fmt: skip

    assert trials
    assert sorted(await ledger.all(), key=lambda t: t.trial_id) == sorted(
        trials, key=lambda t: t.trial_id
    )


async def test_a_train_run_touching_the_holdout_is_refused() -> None:
    feature_study, _ = study()
    definition = FeatureDefinition("momentum", "v1", "", {})

    with pytest.raises(HoldoutViolation):
        await feature_study.run(
            definition, AlwaysOn(), {"NSE:1": uptrend(20)},
            hypothesis=hypothesis(), role=TrialRole.TRAIN,
            dataset_version="sha256:x", study_first=date(2026, 1, 1), study_last=date(2026, 1, 25),
        )  # fmt: skip


async def test_a_test_run_over_exactly_the_holdout_records_trials() -> None:
    feature_study, ledger = study()
    definition = FeatureDefinition("momentum", "v1", "", {})

    trials = await feature_study.run(
        definition, AlwaysOn(), {"NSE:1": uptrend(20)},
        hypothesis=hypothesis(), role=TrialRole.TEST,
        dataset_version="sha256:x", study_first=date(2026, 1, 20), study_last=date(2026, 2, 1),
    )  # fmt: skip

    assert trials
    assert all(t.role is TrialRole.TEST for t in trials)
    assert sorted(await ledger.all(), key=lambda t: t.trial_id) == sorted(
        trials, key=lambda t: t.trial_id
    )


async def test_a_train_run_and_a_test_run_of_the_same_hypothesis_do_not_collide() -> None:
    """Regression guard (found while proving EM-181): a TRAIN run over one window and a TEST run
    over the pre-declared holdout, for the SAME hypothesis/feature/instrument/segment, must record
    as two distinct trials, not raise `DuplicateFeatureTrialError` on the second `append`."""
    feature_study, ledger = study()
    definition = FeatureDefinition("momentum", "v1", "", {})
    declared = hypothesis()

    train_trials = await feature_study.run(
        definition, AlwaysOn(), {"NSE:1": uptrend(20)},
        hypothesis=declared, role=TrialRole.TRAIN,
        dataset_version="sha256:x", study_first=date(2026, 1, 1), study_last=date(2026, 1, 15),
    )  # fmt: skip
    test_trials = await feature_study.run(
        definition, AlwaysOn(), {"NSE:1": uptrend(20)},
        hypothesis=declared, role=TrialRole.TEST,
        dataset_version="sha256:x", study_first=date(2026, 1, 20), study_last=date(2026, 2, 1),
    )  # fmt: skip

    assert train_trials and test_trials
    assert {t.trial_id for t in train_trials}.isdisjoint({t.trial_id for t in test_trials})
    assert len(await ledger.all()) == len(train_trials) + len(test_trials)


async def test_a_test_run_not_exactly_the_holdout_is_refused() -> None:
    feature_study, _ = study()
    definition = FeatureDefinition("momentum", "v1", "", {})

    with pytest.raises(HoldoutViolation):
        await feature_study.run(
            definition, AlwaysOn(), {"NSE:1": uptrend(20)},
            hypothesis=hypothesis(), role=TrialRole.TEST,
            dataset_version="sha256:x", study_first=date(2026, 1, 21), study_last=date(2026, 2, 1),
        )  # fmt: skip
