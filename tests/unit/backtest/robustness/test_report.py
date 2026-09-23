"""EM-182: `RobustnessDocument` renders every gate (including the new PBO one) and its own
evidence section into the JSON-able document a reviewer reads."""

from __future__ import annotations

from decimal import Decimal

from emporos.backtest.robustness.assessment import RobustnessReport
from emporos.backtest.robustness.pbo import PBOReport
from emporos.backtest.robustness.report import RobustnessDocument
from emporos.backtest.robustness.verdict import VerdictPolicy
from tests.unit.backtest.robustness.test_verdict import (
    T,
    concentration,
    dsr,
    evidence,
    monte_carlo,
)

D = Decimal


def _report(pbo: PBOReport) -> RobustnessReport:
    e = evidence(pbo=pbo)
    return RobustnessReport(
        strategy="s", verdict=VerdictPolicy.standard(T).classify(e), evidence=e,
        monte_carlo=monte_carlo(), deflated_sharpe=dsr(), pbo=pbo,
        concentration=concentration(), costs=(), perturbation=None, baseline_net_pnl=D(-50),
    )  # fmt: skip


def test_a_computed_pbo_renders_its_figures() -> None:
    pbo = PBOReport(6, 8, 70, D("0.2"), D("0.05"), None)

    document = RobustnessDocument().of(_report(pbo))

    assert document["pbo"] == {
        "candidate_count": 6, "block_count": 8, "combination_count": 70,
        "probability_of_overfitting": "0.2", "mean_logit": "0.05", "reason": None,
    }  # fmt: skip
    names = {g["name"] for g in document["gates"]}
    assert "does not show CSCV overfitting evidence (PBO)" in names


def test_an_uncomputed_pbo_renders_its_reason() -> None:
    pbo = PBOReport(1, 0, 0, None, None, "1 candidate(s) scored; need 2")

    document = RobustnessDocument().of(_report(pbo))

    assert document["pbo"]["probability_of_overfitting"] is None
    assert document["pbo"]["reason"] == "1 candidate(s) scored; need 2"
