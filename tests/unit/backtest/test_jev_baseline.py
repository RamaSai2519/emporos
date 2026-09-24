from __future__ import annotations

import pytest

from emporos.backtest.jev_baseline import BaselineVerdictFolder
from emporos.backtest.robustness.verdict import GateOutcome
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import ReasonCode
from emporos.domain.verdicts import Standing


def test_a_portfolio_of_validated_strategies_is_validated() -> None:
    report = BaselineVerdictFolder().fold({"a": Standing.VALIDATED, "b": Standing.VALIDATED})

    assert report.verdict is Verdict.VALIDATED
    assert all(g.outcome is GateOutcome.PASS and g.code is None for g in report.gates)


def test_one_rejected_strategy_rejects_the_portfolio() -> None:
    report = BaselineVerdictFolder().fold({"a": Standing.VALIDATED, "b": Standing.REJECTED})

    assert report.verdict is Verdict.REJECTED
    failing = report.of(GateOutcome.FAIL)
    assert [g.name for g in failing] == ["b stands validated"]
    assert failing[0].code is ReasonCode.JEV_BASELINE_NOT_VALIDATED


@pytest.mark.parametrize("standing", [Standing.STALE, Standing.NONE, Standing.INCONCLUSIVE])
def test_anything_unproven_leaves_the_portfolio_inconclusive(standing: Standing) -> None:
    report = BaselineVerdictFolder().fold({"a": Standing.VALIDATED, "b": standing})

    assert report.verdict is Verdict.INCONCLUSIVE
    assert report.of(GateOutcome.UNKNOWN)[0].detail == standing.value


def test_a_rejection_outranks_an_unknown() -> None:
    report = BaselineVerdictFolder().fold({"a": Standing.NONE, "b": Standing.REJECTED})

    assert report.verdict is Verdict.REJECTED


def test_a_baseline_needs_a_strategy() -> None:
    with pytest.raises(ValueError, match="at least one"):
        BaselineVerdictFolder().fold({})
