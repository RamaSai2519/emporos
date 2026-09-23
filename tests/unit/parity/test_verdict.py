"""EM-185: every parity gate answers PASS / FAIL / UNKNOWN, and one verdict follows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.backtest.robustness.verdict import GateOutcome
from emporos.core.errors import ConfigurationError
from emporos.domain.experiments import Verdict
from emporos.parity.config import DEFAULT_PARITY_FILE, ParityThresholds, ParityThresholdsLoader
from emporos.parity.metrics import ParityMetrics, SideTotals
from emporos.parity.verdict import ParityPolicy

D = Decimal
NINE_TENTHS = D("0.9")
T = ParityThresholds.model_validate(
    {
        "min_sessions": 3, "min_matched_trades": 4, "min_signal_agreement": "0.8",
        "max_fill_rate_drop": "0.15", "max_slippage_increase_bps": 5,
        "max_expectancy_drop_fraction": "0.5", "max_drawdown_increase_fraction": "0.5",
        "max_win_rate_drop": "0.10", "max_turnover_ratio": "1.5",
    }
)  # fmt: skip


def side(**over: object) -> SideTotals:
    base: dict[str, object] = {
        "orders": 10, "filled_orders": 10, "trades": 5, "wins": 3, "gross_pnl": D(120),
        "net_pnl": D(100), "charges": D(20), "notional": D(10000), "return_sum": D("0.05"),
        "slippage_bps": (D(2), D(2)), "worst_drawdown": D("0.01"),
    }  # fmt: skip
    base.update(over)
    return SideTotals(**base)  # type: ignore[arg-type]


def metrics(
    paper: SideTotals | None = None,
    backtest: SideTotals | None = None,
    sessions: int = 5,
    matched_trades: int = 5,
    agreement: Decimal | None = NINE_TENTHS,
) -> ParityMetrics:
    return ParityMetrics(
        paper or side(), backtest or side(), 20, 18, matched_trades, sessions, agreement
    )


def outcomes(m: ParityMetrics) -> dict[str, GateOutcome]:
    return {g.name: g.outcome for g in ParityPolicy.standard(T).classify(m).gates}


def outcome_of(m: ParityMetrics, name_part: str) -> GateOutcome:
    (found,) = (o for n, o in outcomes(m).items() if name_part in n)
    return found


def test_identical_sides_over_enough_sample_are_validated() -> None:
    report = ParityPolicy.standard(T).classify(metrics())
    assert report.verdict is Verdict.VALIDATED
    assert all(g.outcome is GateOutcome.PASS for g in report.gates)


@pytest.mark.parametrize(
    ("gate", "paper", "detail"),
    [
        ("fills as often", side(filled_orders=8), "fill"),  # 0.8 vs 1.0: drop 0.2 > 0.15
        ("slips no more", side(slippage_bps=(D(9), D(9))), "slip"),  # +7 bps > 5
        ("earns what", side(return_sum=D("0.02")), "expect"),  # -60% > 50%
        ("draws down no more", side(worst_drawdown=D("0.02")), "draw"),  # +100% > 50%
        ("wins as often", side(wins=2), "win"),  # 0.4 vs 0.6: drop 0.2 > 0.10
        ("trades no more", side(notional=D(20000)), "turn"),  # 2.0x > 1.5x
    ],
)
def test_each_degradation_beyond_its_bound_rejects(
    gate: str, paper: SideTotals, detail: str
) -> None:
    m = metrics(paper=paper)
    assert outcome_of(m, gate) is GateOutcome.FAIL, detail
    assert ParityPolicy.standard(T).classify(m).verdict is Verdict.REJECTED


def test_degradation_just_inside_its_bound_passes() -> None:
    assert outcome_of(metrics(paper=side(filled_orders=9)), "fills as often") is GateOutcome.PASS
    assert (
        outcome_of(metrics(paper=side(slippage_bps=(D(7), D(7)))), "slips no more")
        is GateOutcome.PASS
    )


def test_low_signal_agreement_rejects_and_missing_agreement_is_unknown() -> None:
    assert outcome_of(metrics(agreement=D("0.5")), "paper signals") is GateOutcome.FAIL
    assert outcome_of(metrics(agreement=None), "paper signals") is GateOutcome.UNKNOWN


def test_a_thin_sample_can_never_reject_only_leave_it_inconclusive() -> None:
    bad = metrics(paper=side(filled_orders=1, wins=0), sessions=1, matched_trades=1)
    report = ParityPolicy.standard(T).classify(bad)
    assert report.verdict is Verdict.INCONCLUSIVE
    assert not report.of(GateOutcome.FAIL)
    assert any("only 1 session" in g.detail for g in report.of(GateOutcome.UNKNOWN))


def test_a_thin_sample_that_looks_fine_is_not_validated_either() -> None:
    report = ParityPolicy.standard(T).classify(metrics(sessions=2))
    assert report.verdict is Verdict.INCONCLUSIVE


def test_a_side_with_nothing_to_compare_is_unknown_not_a_pass() -> None:
    empty = side(orders=0, filled_orders=0, trades=0, wins=0, return_sum=D(0), slippage_bps=())
    m = metrics(paper=empty)
    assert outcome_of(m, "fills as often") is GateOutcome.UNKNOWN
    assert outcome_of(m, "earns what") is GateOutcome.UNKNOWN
    assert outcome_of(m, "slips no more") is GateOutcome.UNKNOWN


def test_zero_drawdown_on_both_sides_passes_but_a_new_drawdown_from_none_is_unknown() -> None:
    both_flat = metrics(paper=side(worst_drawdown=D(0)), backtest=side(worst_drawdown=D(0)))
    assert outcome_of(both_flat, "draws down no more") is GateOutcome.PASS
    new = metrics(paper=side(worst_drawdown=D("0.01")), backtest=side(worst_drawdown=D(0)))
    assert outcome_of(new, "draws down no more") is GateOutcome.UNKNOWN


def test_no_backtest_turnover_is_unknown() -> None:
    m = metrics(backtest=side(notional=D(0)))
    assert outcome_of(m, "trades no more") is GateOutcome.UNKNOWN


def test_gate_names_are_unique_and_a_policy_needs_gates() -> None:
    gates = ParityPolicy.standard(T)._gates
    assert len({g.name for g in gates}) == len(gates) == 9
    with pytest.raises(ValueError, match="at least one"):
        ParityPolicy([])
    with pytest.raises(ValueError, match="share a name"):
        ParityPolicy([gates[0], gates[0]])


class TestTheBoundsFile:
    def test_the_committed_file_loads_and_is_what_the_plan_pre_registered(self) -> None:
        t = ParityThresholdsLoader(DEFAULT_PARITY_FILE).load()
        assert (t.min_sessions, t.min_matched_trades) == (10, 30)
        assert (t.min_signal_agreement, t.max_fill_rate_drop) == (D("0.8"), D("0.15"))
        assert (t.max_slippage_increase_bps, t.max_turnover_ratio) == (D(5), D("1.5"))

    def _write(self, tmp_path: Path, text: str) -> Path:
        path = tmp_path / "parity.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_float_is_refused(self, tmp_path: Path) -> None:
        text = DEFAULT_PARITY_FILE.read_text().replace('"0.8"', "0.8")
        with pytest.raises(ConfigurationError, match="min_signal_agreement"):
            ParityThresholdsLoader(self._write(tmp_path, text)).load()

    def test_an_unknown_key_is_refused(self, tmp_path: Path) -> None:
        text = DEFAULT_PARITY_FILE.read_text() + "max_typo: 1\n"
        with pytest.raises(ConfigurationError, match="max_typo"):
            ParityThresholdsLoader(self._write(tmp_path, text)).load()

    def test_a_missing_bound_a_bad_file_and_a_non_mapping_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="min_sessions"):
            ParityThresholdsLoader(self._write(tmp_path, "min_matched_trades: 3\n")).load()
        with pytest.raises(ConfigurationError, match="cannot read"):
            ParityThresholdsLoader(tmp_path / "absent.yaml").load()
        with pytest.raises(ConfigurationError, match="not valid YAML"):
            ParityThresholdsLoader(self._write(tmp_path, "a: [")).load()
        with pytest.raises(ConfigurationError, match="mapping"):
            ParityThresholdsLoader(self._write(tmp_path, "- 1\n")).load()


def test_a_report_knows_its_period_and_refuses_a_backwards_one() -> None:
    from datetime import UTC, datetime

    from emporos.domain.parity import ParityKind, ParityReport

    def make(first: date, last: date) -> ParityReport:
        return ParityReport(
            "s", "h", ParityKind.WEEKLY, first, last, Verdict.INCONCLUSIVE, (), 1, 0, {},
            datetime(2026, 1, 9, tzinfo=UTC),
        )  # fmt: skip

    assert make(date(2026, 1, 5), date(2026, 1, 5)).period == "2026-01-05"
    assert make(date(2026, 1, 5), date(2026, 1, 9)).period == "2026-01-05..2026-01-09"
    with pytest.raises(ValueError, match="cannot end before"):
        make(date(2026, 1, 9), date(2026, 1, 5))
