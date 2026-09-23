"""EM-182/EM-183: `RobustnessDocument` renders every gate (including PBO and the portfolio-
economics edge-survives-cost-error gate) and their own evidence sections into the JSON-able
document a reviewer reads."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from emporos.backtest.feed import FeedWindow
from emporos.backtest.robustness.assessment import RobustnessReport
from emporos.backtest.robustness.holdout import CurationProvenance
from emporos.backtest.robustness.pbo import PBOReport
from emporos.backtest.robustness.performance import RegimeSlice, WindowPerformance
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


def _report(
    pbo: PBOReport,
    window_performance: tuple[WindowPerformance, ...] = (),
    provenance: CurationProvenance | None = None,
) -> RobustnessReport:
    e = evidence(pbo=pbo)
    return RobustnessReport(
        strategy="s", verdict=VerdictPolicy.standard(T).classify(e), evidence=e,
        monte_carlo=monte_carlo(), deflated_sharpe=dsr(), pbo=pbo,
        concentration=concentration(), costs=(), perturbation=None, baseline_net_pnl=D(-50),
        window_performance=window_performance, provenance=provenance,
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


def test_portfolio_economics_renders_the_observed_and_minimum_edge() -> None:
    pbo = PBOReport(6, 8, 70, D("0.2"), D("0.05"), None)

    document = RobustnessDocument().of(_report(pbo))

    assert document["portfolio_economics"] == {
        "observed_edge_bps": "50", "minimum_edge_bps": "10",
    }  # fmt: skip
    names = {g["name"] for g in document["gates"]}
    assert "edge survives plausible cost-model error" in names


def test_windows_render_one_entry_per_window_with_its_regime_breakdown() -> None:
    pbo = PBOReport(6, 8, 70, D("0.2"), D("0.05"), None)
    start = datetime(2026, 3, 2, tzinfo=UTC)
    windows = (
        WindowPerformance(
            index=0, test_start=start, test_end=start, net_pnl=D(100),
            by_regime={"trending": RegimeSlice(5, D(100), D("0.6"))},
        ),
    )  # fmt: skip

    document = RobustnessDocument().of(_report(pbo, window_performance=windows))

    assert document["windows"] == [
        {
            "index": 0, "test_start": start.isoformat(), "test_end": start.isoformat(),
            "net_pnl": "100",
            "by_regime": {"trending": {"count": 5, "net_pnl": "100", "win_rate": "0.6"}},
        }
    ]  # fmt: skip


def test_provenance_renders_research_validation_and_holdout_ranges() -> None:
    pbo = PBOReport(6, 8, 70, D("0.2"), D("0.05"), None)
    a, b, c, d = (datetime(2026, 1, n, tzinfo=UTC) for n in (1, 10, 20, 30))
    provenance = CurationProvenance(FeedWindow(a, b), FeedWindow(b, c), FeedWindow(c, d))

    document = RobustnessDocument().of(_report(pbo, provenance=provenance))

    assert document["provenance"] == {
        "research": {"first": a.isoformat(), "last": b.isoformat()},
        "validation": {"first": b.isoformat(), "last": c.isoformat()},
        "holdout": {"first": c.isoformat(), "last": d.isoformat()},
    }


def test_provenance_is_none_when_not_configured() -> None:
    pbo = PBOReport(6, 8, 70, D("0.2"), D("0.05"), None)

    document = RobustnessDocument().of(_report(pbo))

    assert document["provenance"] is None
