from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.backtest.jev_incremental import JevIncrementalAnalysis, JevIncrementalEvidence
from emporos.backtest.robustness.paired_bootstrap import PairedDayBootstrap
from emporos.backtest.robustness.trials import TrialStatistics
from emporos.core.errors import ConfigurationError
from emporos.opportunity.jev_experiment import JevRunSummary
from tests.support.backtest_metrics import trade
from tests.support.jev_comparison import comparison, metrics, winning_trades

D = Decimal
RATE = D("2.5")  # rupees per 1,000 tokens (a test value, not a price)
TRIALS = TrialStatistics(count=1, scored=1, sharpe_variance=None)


def _days(start: int, step: int, n: int = 30) -> list[str]:
    return [str(start + step * i) for i in range(1, n + 1)]


def _analysis() -> JevIncrementalAnalysis:
    return JevIncrementalAnalysis(PairedDayBootstrap(seed=3, resamples=100))


def _evidence(jev: JevRunSummary | None = None) -> JevIncrementalEvidence:
    baseline = metrics(_days(100000, 100), winning_trades(10, "50"))
    treatment = metrics(_days(100000, 150), winning_trades(8, "80"))
    return _analysis().of(
        comparison(
            baseline, treatment, jev or JevRunSummary(reviews=20, rejections=2, tokens=4000)
        ),
        TRIALS,
        RATE,
    )


def test_deltas_are_treatment_minus_baseline() -> None:
    evidence = _evidence()

    assert evidence.deltas.net_pnl == D("640") - D("500")
    assert evidence.deltas.trade_count == -2
    assert evidence.baseline.trade_count == 10
    assert evidence.treatment.trade_count == 8
    assert evidence.baseline.average_trade == D("50")
    assert evidence.treatment.average_trade == D("80")


def test_jev_cost_is_tokens_over_a_thousand_times_the_declared_rate() -> None:
    evidence = _evidence()

    assert evidence.cost.tokens == 4000
    assert evidence.cost.inr == D("10.0")  # 4000 / 1000 x 2.5
    assert evidence.cost.reviews == 20
    assert evidence.cost.rejections == 2


def test_the_net_of_jev_delta_subtracts_the_cost() -> None:
    evidence = _evidence()

    assert evidence.net_of_jev_pnl_delta == evidence.deltas.net_pnl - D("10.0")
    # per trade: treatment 80 - 10/8 Jev cost per trade - baseline 50
    assert evidence.net_of_jev_average_trade_delta == D("80") - D("1.25") - D("50")


def test_an_unpriced_jev_is_refused_not_treated_as_free() -> None:
    baseline = metrics(_days(100000, 100))
    with pytest.raises(ConfigurationError, match="inr_per_1k_tokens"):
        _analysis().of(comparison(baseline, baseline), TRIALS, None)


def test_the_paired_interval_uses_the_shared_days_and_charges_jev_against_them() -> None:
    evidence = _evidence()

    assert evidence.paired is not None and evidence.paired_reason is None
    assert evidence.paired.days == 30
    assert evidence.paired.mean_daily_return.observed is not None
    assert evidence.paired.mean_daily_return.observed > 0  # the treatment compounds faster


def test_a_cost_larger_than_the_gain_turns_the_paired_delta_negative() -> None:
    evidence = _evidence(JevRunSummary(reviews=20, rejections=2, tokens=100_000_000))

    assert evidence.paired is not None
    assert evidence.paired.mean_daily_return.high < 0
    assert evidence.net_of_jev_pnl_delta < 0


def test_a_short_run_reports_why_there_is_no_paired_interval() -> None:
    baseline = metrics(_days(100000, 100, 5))
    treatment = metrics(_days(100000, 150, 5))

    evidence = _analysis().of(comparison(baseline, treatment), TRIALS, RATE)

    assert evidence.paired is None
    assert evidence.paired_reason is not None and "fewer than" in evidence.paired_reason


def test_sharpe_drawdown_and_charges_are_carried_per_arm() -> None:
    baseline = metrics(["101000", "100000", "102000", "101500"], [trade("10", 0, fees="2")])
    treatment = metrics(["101000", "101500", "102000", "103000"], [trade("10", 0, fees="3")])

    evidence = _analysis().of(comparison(baseline, treatment), TRIALS, RATE)

    assert evidence.baseline.charges == D("2")
    assert evidence.treatment.charges == D("3")
    assert evidence.deltas.charges == D("1")
    assert evidence.baseline.max_drawdown > 0
    assert evidence.treatment.max_drawdown == 0
    assert evidence.deltas.max_drawdown < 0  # negative is an improvement
    assert evidence.deltas.sharpe is not None and evidence.deltas.sharpe > 0


def test_the_deflated_sharpe_is_missing_with_too_few_days_not_zero() -> None:
    evidence = _evidence()

    assert evidence.baseline.deflated_sharpe is not None  # 30 days >= the 10 needed
    short = _analysis().of(
        comparison(metrics(_days(100000, 100, 5)), metrics(_days(100000, 100, 5))), TRIALS, RATE
    )
    assert short.baseline.deflated_sharpe is None
    assert short.deltas.deflated_sharpe is None


def test_regime_deltas_show_where_jev_helped_and_where_it_hurt() -> None:
    baseline = metrics(
        _days(100000, 100),
        by_regime={"trending": winning_trades(4, "50"), "ranging": winning_trades(3, "30")},
    )
    treatment = metrics(
        _days(100000, 100),
        by_regime={"trending": winning_trades(4, "90"), "volatile": winning_trades(2, "10")},
    )

    evidence = _analysis().of(comparison(baseline, treatment), TRIALS, RATE)

    assert evidence.by_regime["trending"].net_pnl_delta == D("160")
    assert evidence.by_regime["ranging"].treatment_count == 0
    assert evidence.by_regime["ranging"].net_pnl_delta == D("-90")
    assert evidence.by_regime["volatile"].baseline_count == 0
    assert evidence.improved_regimes == ("trending", "volatile")
    assert evidence.worsened_regimes == ("ranging",)


def test_the_evidence_records_the_trial_count_and_fingerprint() -> None:
    evidence = _evidence()

    assert evidence.trial_count == 1
    assert evidence.fingerprint.startswith("sha256:")
