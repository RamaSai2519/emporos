"""The backfill is built from the real published records and invents nothing."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.robustness.backfill import PastExperiments
from emporos.backtest.robustness.trials import TrialStatistics
from emporos.domain.experiments import Trial, TrialRole

ROOT = Path(__file__).resolve().parents[4]
NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
STRATEGIES = ("orb_v1", "vwap_reversion_v1", "rsi_pullback_v1")


def curation_records() -> list[dict[str, object]]:
    return [
        record
        for name in STRATEGIES
        for record in json.loads((ROOT / f"docs/strategies/{name}.json").read_text())
    ]


@pytest.fixture(scope="module")
def curation() -> list[Trial]:
    return PastExperiments(NOW).curation(curation_records())


@pytest.fixture(scope="module")
def momentum() -> list[Trial]:
    document = json.loads(
        (ROOT / "docs/backtests/momentum_v1_2025-09-22_2026-09-18.json").read_text()
    )
    return PastExperiments(NOW).momentum(document)


class TestCuration:
    def test_every_candidate_in_every_window_plus_each_chosen_test_run(
        self, curation: list[Trial]
    ) -> None:
        # 3 strategies x 5 windows x (6 candidates trained + 1 chosen tested)
        assert len(curation) == 3 * 5 * 7
        assert len({t.trial_id for t in curation}) == len(curation)

    def test_test_trials_carry_the_recorded_out_of_sample_net_per_window(
        self, curation: list[Trial]
    ) -> None:
        orb = [t for t in curation if t.strategy == "orb_v1" and t.role is TrialRole.TEST]

        assert [t.net_pnl for t in orb] == [
            Decimal(n) for n in ("-1021.76", "-653.98", "-697.91", "-823.37", "-572.41")
        ]
        assert [t.candidate for t in orb] == [
            "r3_t15_s10", "r3_t15_s10", "r3_t15_s10", "r3_t20_s15", "r6_t30_s10",
        ]  # fmt: skip

    def test_training_scores_the_record_never_kept_stay_unknown(
        self, curation: list[Trial]
    ) -> None:
        training = [t for t in curation if t.role is TrialRole.TRAIN]

        assert all(t.net_pnl is None and t.daily_sharpe is None for t in training)
        assert all("score not kept" in t.note for t in training)

    def test_the_search_is_counted_even_though_its_spread_is_not_known(
        self, curation: list[Trial]
    ) -> None:
        statistics = TrialStatistics.of(curation)

        assert (statistics.count, statistics.scored, statistics.sharpe_variance) == (105, 0, None)


class TestMomentum:
    def test_the_acceptance_run_and_the_two_variants_the_readme_reports(
        self, momentum: list[Trial]
    ) -> None:
        assert [t.candidate for t in momentum] == ["as-shipped", "no-buffer", "paper-fill-defaults"]
        shipped = momentum[0]
        assert shipped.run_id == "bt-cc1930a4acb88673-20250922"
        assert shipped.config_hash is not None and shipped.config_hash.startswith("sha256:cc1930")
        assert (shipped.trade_count, shipped.net_pnl) == (223, Decimal("-28416.54"))
        assert shipped.daily_sharpe == DecimalMath.divide(
            Decimal("-6.01665464"), DecimalMath.sqrt(Decimal(252))
        )

    def test_variants_without_a_kept_run_have_no_run_id(self, momentum: list[Trial]) -> None:
        assert all(t.run_id is None for t in momentum[1:])
        assert momentum[1].net_pnl == Decimal("-18674.37")

    def test_ids_do_not_collide_with_the_curation_backfill(
        self, momentum: list[Trial], curation: list[Trial]
    ) -> None:
        assert not {t.trial_id for t in momentum} & {t.trial_id for t in curation}
