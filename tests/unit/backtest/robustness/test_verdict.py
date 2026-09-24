"""Each gate on its own, and the classification rule: any FAIL rejects, else any UNKNOWN is
inconclusive, else validated. Thin evidence can keep a strategy from validation, never invent a
rejection or a validation."""

from dataclasses import replace
from decimal import Decimal

import pytest

from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.robustness.concentration import ConcentrationReport
from emporos.backtest.robustness.deflated_sharpe import DeflatedSharpeReport
from emporos.backtest.robustness.monte_carlo import (
    Interval,
    MonteCarloReport,
    ObservedFigures,
    ResampledDistribution,
)
from emporos.backtest.robustness.pbo import PBOReport
from emporos.backtest.robustness.perturbation import NeighbourRun, PerturbationReport
from emporos.backtest.robustness.verdict import (
    BeatsBaseline,
    BeatsLuck,
    BeatsOverfitting,
    DrawdownWithinBudget,
    EnoughHistory,
    EnoughTrades,
    Evidence,
    Gate,
    GateOutcome,
    NotConcentrated,
    ParameterStability,
    ProfitAfterCosts,
    RegimeDiversity,
    SurvivesAdverseCosts,
    SurvivesCostError,
    VerdictPolicy,
    WalkForwardWindows,
)
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import ReasonCode

D = Decimal
T = BenchmarkLoader().load().verdict
PASS, FAIL, UNKNOWN = GateOutcome.PASS, GateOutcome.FAIL, GateOutcome.UNKNOWN


def monte_carlo(
    p_positive: str = "0.99", net: str = "1000", conclusive: bool = True
) -> MonteCarloReport:
    observed = ObservedFigures(D(net), D("0.01"), D("1.5"), D("0.05"))
    if not conclusive:
        return MonteCarloReport(10, 5000, 1, D("0.95"), observed, None, "10 trades is too few")
    interval = Interval(D(net), D(net) - 500, D(net) + 500)
    distribution = ResampledDistribution(
        interval, interval, None, 0, D(p_positive), interval, D("0.02")
    )
    return MonteCarloReport(200, 5000, 1, D("0.95"), observed, distribution, None)


def dsr(value: str | None = "0.99") -> DeflatedSharpeReport:
    if value is None:
        return DeflatedSharpeReport(100, 35, None, None, None, None, None, "spread unknown")
    return DeflatedSharpeReport(
        500, 35, D("0.1"), D("1.6"), D("0.05"), D("0.999"), D(value), None
    )  # fmt: skip


def concentration(
    instrument: str = "0.2", month: str = "0.2", top: str = "0.3"
) -> ConcentrationReport:
    return ConcentrationReport(D(1000), "NSE:1", D(instrument), "2026-03", D(month), 5, D(top))


def pbo(value: str | None = "0.1") -> PBOReport:
    if value is None:
        return PBOReport(2, 4, 6, None, None, "not computed")
    return PBOReport(6, 8, 70, D(value), D("0.05"), None)


def evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "trade_count": 400, "net_pnl": D(1000), "adverse_net_pnl": D(400), "history_days": 800,
        "window_nets": tuple(D(n) for n in (200, 300, 250, 250)),
        "worst_window_drawdown": D("0.04"), "monte_carlo": monte_carlo(), "deflated_sharpe": dsr(),
        "pbo": pbo(),
        "observed_edge_bps": D(50), "minimum_edge_bps": D(10),
        "concentration": concentration(),
        "perturbation": PerturbationReport(tuple(NeighbourRun(0, str(n), D(10)) for n in range(4))),
        "baseline_net_pnl": D(-50),
        "regimes_covered": frozenset({"trending", "ranging"}),
    }  # fmt: skip
    values.update(overrides)
    return Evidence(**values)  # type: ignore[arg-type]


class TestProfitAfterCosts:
    gate = ProfitAfterCosts(T)

    def test_passes_when_a_profit_is_all_but_certain(self) -> None:
        assert self.gate.assess(evidence()).outcome is PASS

    def test_fails_when_a_loss_is_demonstrated(self) -> None:
        e = evidence(net_pnl=D(-900), monte_carlo=monte_carlo("0.01", "-900"))

        assert self.gate.assess(e).outcome is FAIL

    def test_is_unknown_in_between(self) -> None:
        assert self.gate.assess(evidence(monte_carlo=monte_carlo("0.70"))).outcome is UNKNOWN

    def test_is_unknown_on_thin_evidence_even_for_a_big_loss(self) -> None:
        e = evidence(net_pnl=D(-900), monte_carlo=monte_carlo(conclusive=False, net="-900"))

        result = self.gate.assess(e)

        assert result.outcome is UNKNOWN and "too few" in result.detail

    def test_a_probable_profit_that_is_not_a_profit_does_not_pass(self) -> None:
        assert self.gate.assess(evidence(net_pnl=D(-1))).outcome is not PASS


class TestSimpleGates:
    def test_adverse_costs(self) -> None:
        gate = SurvivesAdverseCosts()

        assert gate.assess(evidence()).outcome is PASS
        assert gate.assess(evidence(adverse_net_pnl=D(-5))).outcome is FAIL
        assert gate.assess(evidence(net_pnl=D(-5), adverse_net_pnl=D(-50))).outcome is UNKNOWN

    def test_walk_forward_windows(self) -> None:
        gate = WalkForwardWindows(T)

        assert gate.assess(evidence()).outcome is PASS
        assert (
            gate.assess(evidence(window_nets=tuple(D(n) for n in (1, -1, -1, -1)))).outcome is FAIL
        )
        assert gate.assess(evidence(window_nets=(D(1), D(1)))).outcome is UNKNOWN  # under 3 windows

    def test_trades_and_history_only_ever_hold_back_validation(self) -> None:
        assert EnoughTrades(T).assess(evidence(trade_count=149)).outcome is UNKNOWN
        assert EnoughTrades(T).assess(evidence(trade_count=150)).outcome is PASS
        assert EnoughHistory(T).assess(evidence(history_days=499)).outcome is UNKNOWN
        assert EnoughHistory(T).assess(evidence(history_days=500)).outcome is PASS

    def test_drawdown_budget_is_a_hard_limit(self) -> None:
        gate = DrawdownWithinBudget(T)

        assert gate.assess(evidence(worst_window_drawdown=D("0.10"))).outcome is PASS
        assert gate.assess(evidence(worst_window_drawdown=D("0.11"))).outcome is FAIL

    def test_parameter_stability(self) -> None:
        gate = ParameterStability(T)
        half = PerturbationReport((NeighbourRun(0, "a", D(1)), NeighbourRun(0, "b", D(-1))))
        under = PerturbationReport(
            (NeighbourRun(0, "a", D(1)), NeighbourRun(0, "b", D(-1)), NeighbourRun(0, "c", D(-1)))
        )

        assert gate.assess(evidence(perturbation=half)).outcome is PASS  # 50% needed, 50% seen
        assert gate.assess(evidence(perturbation=under)).outcome is FAIL
        assert gate.assess(evidence(perturbation=None)).outcome is UNKNOWN
        assert gate.assess(evidence(perturbation=PerturbationReport(()))).outcome is UNKNOWN

    def test_deflated_sharpe_never_rejects_only_withholds(self) -> None:
        gate = BeatsLuck(T)

        assert gate.assess(evidence()).outcome is PASS
        assert gate.assess(evidence(deflated_sharpe=dsr("0.60"))).outcome is UNKNOWN
        assert gate.assess(evidence(deflated_sharpe=dsr(None))).outcome is UNKNOWN

    def test_overfitting_evidence_is_unknown_when_not_computed_but_fails_when_conclusive(
        self,
    ) -> None:
        gate = BeatsOverfitting(T)  # the real config file: max_pbo defaults to 1 (no limit)

        assert gate.assess(evidence()).outcome is PASS
        assert gate.assess(evidence(pbo=pbo(None))).outcome is UNKNOWN
        strict = BeatsOverfitting(T.model_copy(update={"max_pbo": D("0.3")}))
        assert strict.assess(evidence(pbo=pbo("0.2"))).outcome is PASS
        assert strict.assess(evidence(pbo=pbo("0.8"))).outcome is FAIL

    def test_edge_survives_cost_error_needs_the_configured_safety_margin(self) -> None:
        gate = SurvivesCostError(T)  # the real config file: margin defaults to 1.5x

        assert gate.assess(evidence()).outcome is PASS  # 50 bps observed vs 10 bps * 1.5 = 15
        assert gate.assess(evidence(observed_edge_bps=D(12))).outcome is FAIL  # below 15
        assert gate.assess(evidence(observed_edge_bps=D(15))).outcome is PASS  # exactly at 15
        assert gate.assess(evidence(observed_edge_bps=None)).outcome is UNKNOWN
        assert gate.assess(evidence(minimum_edge_bps=None)).outcome is UNKNOWN

    def test_baseline(self) -> None:
        gate = BeatsBaseline()

        assert gate.assess(evidence()).outcome is PASS
        assert gate.assess(evidence(baseline_net_pnl=D(5000))).outcome is FAIL
        assert gate.assess(evidence(baseline_net_pnl=None)).outcome is UNKNOWN
        thin = evidence(baseline_net_pnl=D(5000), monte_carlo=monte_carlo(conclusive=False))
        assert gate.assess(thin).outcome is UNKNOWN


class TestRegimeDiversityGate:
    def test_the_configured_threshold_requires_that_many_distinct_regimes(self) -> None:
        gate = RegimeDiversity(T)  # the real config file: EM-184 sets min_regimes to 2

        assert gate.assess(evidence(regimes_covered=frozenset({"trending"}))).outcome is FAIL
        assert (
            gate.assess(evidence(regimes_covered=frozenset({"trending", "ranging"}))).outcome
            is PASS
        )

    def test_no_regime_data_is_unknown_not_rejected(self) -> None:
        gate = RegimeDiversity(T)

        assert gate.assess(evidence(regimes_covered=frozenset())).outcome is UNKNOWN

    def test_min_regimes_at_or_below_one_is_an_explicit_opt_out(self) -> None:
        opted_out = T.model_copy(update={"min_regimes": 1})
        gate = RegimeDiversity(opted_out)

        assert gate.assess(evidence(regimes_covered=frozenset())).outcome is PASS

    def test_a_regime_specific_strategy_is_exempt_regardless_of_coverage(self) -> None:
        gate = RegimeDiversity(T)

        e = evidence(regimes_covered=frozenset(), regime_specific=True)

        assert gate.assess(e).outcome is PASS


class TestConcentrationGate:
    gate = NotConcentrated(T)

    def test_spread_profit_passes(self) -> None:
        assert self.gate.assess(evidence()).outcome is PASS

    @pytest.mark.parametrize("field", ["instrument", "month", "top"])
    def test_any_one_share_over_its_limit_fails(self, field: str) -> None:
        e = evidence(concentration=concentration(**{field: "0.51"}))

        result = self.gate.assess(e)

        assert result.outcome is FAIL and "51%" in result.detail

    def test_no_profit_means_nothing_to_attribute(self) -> None:
        empty = ConcentrationReport(D(-5), None, None, None, None, 5, None)

        assert self.gate.assess(evidence(concentration=empty)).outcome is UNKNOWN


class TestClassification:
    policy = VerdictPolicy.standard(T)

    def test_every_gate_passing_validates(self) -> None:
        report = self.policy.classify(evidence())

        assert report.verdict is Verdict.VALIDATED
        assert len(report.gates) == 13 and all(g.outcome is PASS for g in report.gates)

    def test_one_fail_rejects_however_much_else_passes(self) -> None:
        assert (
            self.policy.classify(evidence(worst_window_drawdown=D("0.5"))).verdict
            is Verdict.REJECTED
        )

    def test_a_fail_beats_an_unknown(self) -> None:
        e = evidence(worst_window_drawdown=D("0.5"), trade_count=10)

        assert self.policy.classify(e).verdict is Verdict.REJECTED

    def test_an_unknown_without_a_fail_is_inconclusive(self) -> None:
        report = self.policy.classify(evidence(history_days=245))

        assert report.verdict is Verdict.INCONCLUSIVE
        assert [g.name for g in report.of(UNKNOWN)] == ["enough history"]

    def test_a_demonstrated_loss_is_rejected_even_with_thin_history(self) -> None:
        e = evidence(
            net_pnl=D(-3769), adverse_net_pnl=D(-5943), history_days=245, trade_count=40,
            monte_carlo=monte_carlo("0.0", "-3769"),
            concentration=ConcentrationReport(D(-3769), None, None, None, None, 5, None),
            perturbation=None, baseline_net_pnl=None,
        )  # fmt: skip

        assert self.policy.classify(e).verdict is Verdict.REJECTED

    def test_a_policy_needs_gates(self) -> None:
        with pytest.raises(ValueError):
            VerdictPolicy([])

    def test_a_new_check_is_a_new_gate_not_an_edit(self) -> None:
        class Never:
            name = "never"
            code = ReasonCode.NET_PNL_NOT_REAL

            def assess(self, evidence: Evidence):  # type: ignore[no-untyped-def]
                from emporos.backtest.robustness.verdict import GateResult

                return GateResult("never", FAIL, "always")

        gates: list[Gate] = [EnoughTrades(T), Never()]

        assert VerdictPolicy(gates).classify(evidence()).verdict is Verdict.REJECTED
        assert replace(evidence(), trade_count=1).trade_count == 1


class TestGateReasonCodes:
    """A finding says which rule spoke, in a form a report can group by (EM-188)."""

    def test_every_standard_gate_has_its_own_code(self) -> None:
        gates = VerdictPolicy.standard(T).gates
        codes = [g.code for g in gates]

        assert len(codes) == len(set(codes))

    def test_a_result_carries_the_code_of_the_gate_that_produced_it(self) -> None:
        report = VerdictPolicy.standard(T).classify(evidence())

        gates = VerdictPolicy.standard(T).gates
        assert [r.code for r in report.gates] == [g.code for g in gates]
        assert all(r.code is not None for r in report.gates)
