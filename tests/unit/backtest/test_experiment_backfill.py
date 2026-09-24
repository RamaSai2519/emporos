"""EM-188: published curation reports become experiment reports without fabricating anything."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from emporos.backtest.experiment_backfill import BackfillSource, ReportBackfill
from emporos.backtest.experiment_report import RegimePooling
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.robustness.verdict import VerdictPolicy
from emporos.domain.research_experiments import (
    BACKFILLED_NOT_PREDECLARED,
    ExperimentOutcomeLabel,
    FindingOutcome,
    ReasonCode,
    VersionStamp,
)

COMMITTED = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
GATE_CODES = {
    g.name: g.code for g in VerdictPolicy.standard(BenchmarkLoader().load().verdict).gates
}


def load(relative: str) -> dict[str, Any]:
    [entry] = json.loads(Path(relative).read_text(encoding="utf-8"))
    return entry


def source(document: dict[str, Any], tag: str = "test-run") -> BackfillSource:
    return BackfillSource(document, tag, "docs/strategies/x.json", COMMITTED)


def backfill(document: dict[str, Any], tag: str = "test-run"):  # type: ignore[no-untyped-def]
    s = source(document, tag)
    builder = ReportBackfill(GATE_CODES)
    return builder.build(s, builder.declaration(s), VersionStamp())


class TestWithoutRobustness:
    """The first three curation reports, which predate the robustness assessment."""

    def test_the_checks_stand_in_as_reasons_with_no_code(self) -> None:
        report = backfill(load("docs/strategies/orb_v1.json"))

        assert report.outcome is ExperimentOutcomeLabel.REJECTED
        assert all(r.code is None for r in report.reasons)
        assert FindingOutcome.FAIL in {r.outcome for r in report.reasons}

    def test_what_was_recorded_is_carried_and_nothing_else_is_invented(self) -> None:
        recorded = load("docs/strategies/orb_v1.json")

        m = backfill(recorded).metrics

        assert m.net_pnl == Decimal(recorded["out_of_sample"]["net_pnl"])
        assert m.gross_pnl == Decimal(recorded["out_of_sample"]["gross_pnl"])
        assert m.trade_count == recorded["out_of_sample"]["trades"]
        assert m.profit_factor == Decimal(recorded["out_of_sample"]["profit_factor"])
        assert m.max_drawdown == max(
            Decimal(d) for d in recorded["out_of_sample"]["window_max_drawdowns"]
        )
        # never recorded, so never stated:
        assert m.expectancy is None and m.sharpe is None and m.deflated_sharpe is None
        assert m.pbo is None and m.costs is None and not m.by_regime

    def test_the_windows_come_from_the_recorded_dates_and_nets(self) -> None:
        recorded = load("docs/strategies/orb_v1.json")

        report = backfill(recorded)

        assert [w.net_pnl for w in report.metrics.by_window] == [
            Decimal(n) for n in recorded["out_of_sample"]["window_nets"]
        ]
        assert report.periods.validation is not None
        assert report.periods.train is None and report.periods.holdout is None

    def test_a_passing_report_without_robustness_is_only_inconclusive(self) -> None:
        recorded = {**load("docs/strategies/orb_v1.json"), "passed": True}

        assert backfill(recorded).outcome is ExperimentOutcomeLabel.INCONCLUSIVE


class TestWithRobustness:
    def test_the_recorded_verdict_and_gates_are_kept_and_coded_by_name(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")

        report = backfill(recorded)

        assert report.outcome.value == recorded["robustness"]["verdict"].replace(
            "validated", "accepted"
        )
        assert [r.name for r in report.reasons] == [
            g["name"] for g in recorded["robustness"]["gates"]
        ]
        assert all(r.code is not None for r in report.reasons)

    def test_a_recorded_code_wins_over_the_name_lookup(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")
        recorded["robustness"]["gates"][0]["code"] = "adverse_costs"

        report = backfill(recorded)

        assert report.reasons[0].code is ReasonCode.ADVERSE_COSTS

    def test_an_unknown_gate_name_has_no_code_rather_than_a_guess(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")
        recorded["robustness"]["gates"][0]["name"] = "a gate that was later renamed"

        assert backfill(recorded).reasons[0].code is None

    def test_sharpe_and_dsr_come_from_the_recorded_evidence(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")

        m = backfill(recorded).metrics

        dsr = recorded["robustness"]["deflated_sharpe"]
        assert m.sharpe == (
            None if dsr["annualised_sharpe"] is None else Decimal(dsr["annualised_sharpe"])
        )
        assert m.deflated_sharpe == (
            None if dsr["deflated_sharpe"] is None else Decimal(dsr["deflated_sharpe"])
        )
        assert m.pbo is None  # this report predates PBO

    def test_the_recorded_robustness_evidence_is_carried_verbatim(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")

        report = backfill(recorded)

        assert report.supporting["robustness"] == recorded["robustness"]
        assert report.supporting["source"] == "docs/strategies/x.json"

    def test_recorded_provenance_becomes_the_periods(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")
        recorded["robustness"]["provenance"] = {
            "research": {"first": "2025-09-22", "last": "2026-06-26"},
            "validation": {"first": "2026-01-30", "last": "2026-08-07"},
            "holdout": {"first": "2026-08-08", "last": "2026-09-18"},
        }

        periods = backfill(recorded).periods

        assert periods.holdout is not None and periods.holdout.first == date(2026, 8, 8)
        assert periods.train is not None and periods.validation is not None

    def test_recorded_windows_and_regimes_are_pooled(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")
        recorded["robustness"]["windows"] = [
            {
                "index": 0,
                "test_start": "2026-01-29T18:30:00+00:00",
                "test_end": "2026-03-12T18:30:00+00:00",
                "net_pnl": "-10.5",
                "by_regime": {"range": {"count": 4, "net_pnl": "-10.5", "win_rate": "0.25"}},
            },
            {
                "index": 1,
                "test_start": "2026-03-12T18:30:00+00:00",
                "test_end": "2026-04-23T18:30:00+00:00",
                "net_pnl": "5",
                "by_regime": {"range": {"count": 6, "net_pnl": "5", "win_rate": "0.5"}},
            },
        ]

        m = backfill(recorded).metrics

        assert [(w.index, w.first, w.last) for w in m.by_window] == [
            (0, date(2026, 1, 30), date(2026, 3, 12)),
            (1, date(2026, 3, 13), date(2026, 4, 23)),
        ]
        assert m.by_regime["range"].count == 10
        assert m.by_regime["range"].net_pnl == Decimal("-5.5")
        assert m.by_regime["range"].win_rate == Decimal("0.4")  # 1 win of 4, 3 of 6 -> 4 of 10


class TestNeverFabricates:
    def test_the_declaration_says_it_was_not_predeclared_and_uses_the_commit_date(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")
        s = source(recorded, "benchmark-50k")

        d = ReportBackfill(GATE_CODES).declaration(s)

        assert not d.is_predeclared
        assert d.economic_rationale == "backfilled: not pre-declared"
        assert d.slug == "orb-v1-benchmark-50k"
        assert d.declared_at == COMMITTED
        assert dict(d.parameter_grid) == {"candidate": tuple(recorded["candidates"])}

    def test_every_backfilled_report_carries_the_note_and_no_versions(self) -> None:
        report = backfill(load("docs/strategies/benchmark_50k/orb_v1.json"))

        assert BACKFILLED_NOT_PREDECLARED in report.notes
        assert report.versions == VersionStamp()

    def test_no_backfilled_report_is_ever_accepted(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")
        recorded["robustness"]["verdict"] = "validated"
        for gate in recorded["robustness"]["gates"]:
            gate["outcome"] = "pass"

        report = backfill(recorded)

        assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
        assert report.reasons[-1].code is ReasonCode.NOT_PREDECLARED

    def test_the_id_is_stable_for_the_same_source(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")

        assert backfill(recorded).experiment_id == backfill(recorded).experiment_id

    def test_two_runs_of_one_strategy_are_two_experiments(self) -> None:
        recorded = load("docs/strategies/benchmark_50k/orb_v1.json")

        assert (
            backfill(recorded, "benchmark-50k").experiment_id
            != backfill(recorded, "em171-refresh").experiment_id
        )


class TestRegimePooling:
    def test_an_empty_pool_is_empty(self) -> None:
        assert RegimePooling().pool([]) == {}

    def test_a_regime_with_no_trades_is_dropped(self) -> None:
        assert RegimePooling().pool([("range", 0, Decimal(0), None)]) == {}
