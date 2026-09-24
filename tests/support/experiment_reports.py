"""A fixed, fully-populated experiment report: what the schema-drift golden renders and what the
registry tests publish. Built by hand, not from a run, so it never moves unless the schema does."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.domain.research_experiments import (
    CostBreakdown,
    CostModelVersion,
    DatasetVersion,
    DatePair,
    ExperimentDeclaration,
    ExperimentFamily,
    ExperimentMetrics,
    ExperimentOutcomeLabel,
    ExperimentPeriods,
    ExperimentReport,
    FindingOutcome,
    ReasonCode,
    ReasonFinding,
    RegimeMetrics,
    VersionStamp,
    WindowMetrics,
)

D = Decimal


def sample_declaration(
    slug: str = "orb-breakout",
    family: ExperimentFamily = ExperimentFamily.STRATEGY,
    declared_at: datetime | None = None,
) -> ExperimentDeclaration:
    return ExperimentDeclaration(
        family=family,
        slug=slug,
        hypothesis="Opening-range breakouts continue on high-volume days.",
        economic_rationale="Order-flow imbalance at the open persists for the first hour.",
        falsification="Net expectancy after costs is not positive out of sample.",
        parameter_grid={"range_bars": ("3", "6"), "target_r": ("1.5", "3.0")},
        feature_versions={"opening_range": "1"},
        declared_at=declared_at or datetime(2026, 9, 24, 3, 0, tzinfo=UTC),
    )


def sample_report(
    slug: str = "orb-breakout",
    family: ExperimentFamily = ExperimentFamily.STRATEGY,
    outcome: ExperimentOutcomeLabel = ExperimentOutcomeLabel.INCONCLUSIVE,
    declared_at: datetime | None = None,
) -> ExperimentReport:
    declaration = sample_declaration(slug, family, declared_at)
    passes = outcome is ExperimentOutcomeLabel.ACCEPTED
    return ExperimentReport(
        experiment_id=ExperimentIdMinter().mint(declaration),
        declaration=declaration,
        versions=VersionStamp(
            behaviour_hash="sha256:" + "ab" * 32,
            candidate_behaviour_hashes={"r3_t15": "sha256:" + "cd" * 32},
            dataset=DatasetVersion(
                "sha256:" + "01" * 32,
                "sha256:" + "02" * 32,
                "sha256:" + "03" * 32,
                "5m",
                date(2025, 9, 22),
                date(2026, 8, 7),
            ),  # fmt: skip
            cost_model=CostModelVersion("angelone-2026-09-20", D("5"), "sha256:" + "04" * 32),
            code_revision="1748aab",
        ),
        periods=ExperimentPeriods(
            train=DatePair(date(2025, 9, 22), date(2026, 6, 26)),
            validation=DatePair(date(2026, 1, 30), date(2026, 8, 7)),
            holdout=DatePair(date(2026, 8, 8), date(2026, 9, 18)),
        ),
        metrics=ExperimentMetrics(
            gross_pnl=D("5210.40"),
            net_pnl=D("3120.75"),
            expectancy=D("8.6688"),
            profit_factor=D("1.4210"),
            max_drawdown=D("0.0412"),
            sharpe=D("1.3721"),
            deflated_sharpe=D("0.9314"),
            pbo=D("0.2200"),
            trade_count=360,
            win_rate=D("0.5111"),
            by_regime={
                "trend_up": RegimeMetrics(210, D("2400.10"), D("0.55")),
                "range": RegimeMetrics(150, D("720.65"), D("0.4533")),
            },
            by_window=(
                WindowMetrics(0, date(2026, 1, 30), date(2026, 3, 12), D("1200.50")),
                WindowMetrics(1, date(2026, 3, 13), date(2026, 4, 23), D("-310.25")),
            ),
            costs=CostBreakdown(
                brokerage=D("900.00"),
                statutory=D("700.00"),
                spread=D("310.40"),
                slippage=D("479.25"),
                total=D("2389.65"),
                per_trade_bps=D("6.6379"),
                per_trade_inr=D("6.6379"),
            ),  # fmt: skip
        ),
        outcome=outcome,
        reasons=(
            ReasonFinding(
                ReasonCode.NET_PNL_NOT_REAL,
                "profit after costs is real",
                FindingOutcome.PASS,
                "net 3120.75, P(net > 0) 0.981",
            ),
            ReasonFinding(
                ReasonCode.DSR_BELOW_THRESHOLD,
                "beats the luck of the search (Deflated Sharpe)",
                FindingOutcome.PASS if passes else FindingOutcome.UNKNOWN,
                "DSR 0.931 over 35 trials, 0.95 needed",
            ),
        ),  # fmt: skip
        notes=("the final holdout was reserved and is not evaluated by curation",),
        supporting={"robustness": {"verdict": "inconclusive", "gates": []}},
    )


def another_report(slug: str, declared_at: datetime, family: ExperimentFamily) -> ExperimentReport:
    """A second, distinguishable report for ordering tests."""
    return replace(
        sample_report(slug=slug, family=family, declared_at=declared_at),
        metrics=ExperimentMetrics(trade_count=12),
        notes=(),
        supporting={},
    )
