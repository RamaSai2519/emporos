"""Experiment reports for the research families that have no P&L: feature, cross-sectional and
lead-lag studies (EM-188).

Their evidence is rows in an append-only trial ledger (a rank IC, a cost-adjusted expectancy, a
t-statistic, a sample size), not a walk-forward backtest, so P&L, drawdown, Sharpe and PBO are
"n/a" in the report. What every family shares is the outcome and the machine-readable reasons, so
the outcome rule is written down here, in small gates, and applies to a hypothesis's holdout only:

    ACCEPTED      every gate passes
    REJECTED      any gate FAILS   (enough evidence to say the edge is not there)
    INCONCLUSIVE  otherwise        (too little evidence either way)

The three ledgers differ only in field names, so each is adapted once into the same `EvidenceRow`
and one builder judges them all: a new study type is a new adapter, not an edit of the builder.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.backtest.experiment_report import PredeclarationCap
from emporos.domain.cross_sectional_trials import CrossSectionalTrial
from emporos.domain.experiments import TrialRole, Verdict
from emporos.domain.feature_trials import FeatureTrial
from emporos.domain.hypotheses import HypothesisDeclaration
from emporos.domain.lead_lag_trials import LeadLagTrial
from emporos.domain.research_experiments import (
    BACKFILLED_RATIONALE,
    CostModelVersion,
    DatePair,
    ExperimentDeclaration,
    ExperimentFamily,
    ExperimentMetrics,
    ExperimentPeriods,
    ExperimentReport,
    FindingOutcome,
    JsonValue,
    ReasonCode,
    ReasonFinding,
    VersionStamp,
    outcome_of,
)
from emporos.research.cross_sectional_ledger import CrossSectionalTrialLedger
from emporos.research.hypotheses import HypothesisRegistry
from emporos.research.lead_lag_ledger import LeadLagTrialLedger
from emporos.research.ledger import FeatureTrialLedger

_ZERO = Decimal(0)
LEDGER_FAMILIES = frozenset(
    {ExperimentFamily.FEATURE, ExperimentFamily.CROSS_SECTIONAL, ExperimentFamily.LEAD_LAG}
)


@dataclass(frozen=True)
class EvidenceRow:
    """One ledger trial, reduced to what an outcome rule may look at."""

    role: TrialRole
    regime_axis: str | None  # None = pooled across every regime
    regime_label: str | None
    sample_size: int
    net_expectancy: Decimal | None  # after the cost model; per observation, not P&L
    t_statistic: Decimal | None
    label: str  # which feature/horizon/tail/predictor produced it

    @property
    def is_pooled(self) -> bool:
        return self.regime_axis is None

    @property
    def segment(self) -> tuple[str, str] | None:
        if self.regime_axis is None or self.regime_label is None:
            return None
        return self.regime_axis, self.regime_label


@dataclass(frozen=True)
class LedgerExperiment:
    """A hypothesis and every ledger row recorded against it: the source a report is built from."""

    family: ExperimentFamily
    hypothesis: HypothesisDeclaration
    rows: tuple[EvidenceRow, ...]
    dataset_versions: tuple[str, ...]
    cost_models: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceThresholds:
    """When a holdout row counts as evidence. Fixed in advance, like `SelectionCriteria`."""

    min_abs_t: Decimal = Decimal(2)
    min_sample: int = 100
    min_regimes: int = 2

    def __post_init__(self) -> None:
        if self.min_abs_t <= _ZERO or self.min_sample < 1 or self.min_regimes < 1:
            raise ValueError("evidence thresholds must be positive")

    def evaluable(self, row: EvidenceRow) -> bool:
        return (
            row.sample_size >= self.min_sample
            and row.net_expectancy is not None
            and row.t_statistic is not None
        )

    def shows_edge(self, row: EvidenceRow) -> bool:
        return (
            row.net_expectancy is not None
            and row.t_statistic is not None
            and row.net_expectancy > _ZERO
            and abs(row.t_statistic) >= self.min_abs_t
        )


class EvidenceGate(Protocol):
    def assess(self, experiment: LedgerExperiment) -> ReasonFinding: ...


def _holdout_rows(experiment: LedgerExperiment, *, pooled: bool) -> list[EvidenceRow]:
    return [r for r in experiment.rows if r.role is TrialRole.TEST and r.is_pooled == pooled]


class HoldoutEvaluated:
    """The holdout was actually scored: at least one pooled TEST row exists."""

    def assess(self, experiment: LedgerExperiment) -> ReasonFinding:
        found = len(_holdout_rows(experiment, pooled=True))
        outcome = FindingOutcome.PASS if found else FindingOutcome.UNKNOWN
        return ReasonFinding(
            ReasonCode.HOLDOUT_NOT_EVALUATED,
            "the declared holdout was evaluated",
            outcome,
            f"{found} pooled holdout trial(s) recorded",
        )


class EnoughHoldoutObservations:
    def __init__(self, thresholds: EvidenceThresholds) -> None:
        self._t = thresholds

    def assess(self, experiment: LedgerExperiment) -> ReasonFinding:
        rows = _holdout_rows(experiment, pooled=True)
        smallest = min((r.sample_size for r in rows), default=0)
        ok = bool(rows) and smallest >= self._t.min_sample
        return ReasonFinding(
            ReasonCode.TOO_FEW_TRADES,
            "enough holdout observations",
            FindingOutcome.PASS if ok else FindingOutcome.UNKNOWN,
            f"smallest pooled holdout sample {smallest}, {self._t.min_sample} needed",
        )


class HoldoutEdgeSignificant:
    """Every pooled holdout row, every horizon tried, must show a positive cost-adjusted
    expectancy with |t| at the threshold: a hypothesis is not accepted on its best horizon."""

    def __init__(self, thresholds: EvidenceThresholds) -> None:
        self._t = thresholds

    def assess(self, experiment: LedgerExperiment) -> ReasonFinding:
        rows = _holdout_rows(experiment, pooled=True)
        detail = (
            f"{sum(1 for r in rows if self._t.shows_edge(r))} of {len(rows)} pooled holdout "
            f"trial(s) positive after costs with |t| >= {self._t.min_abs_t}"
        )
        if not rows:
            return self._finding(FindingOutcome.UNKNOWN, "no pooled holdout trial")
        failing = [r for r in rows if self._t.evaluable(r) and not self._t.shows_edge(r)]
        if failing:
            return self._finding(FindingOutcome.FAIL, detail)
        if any(not self._t.evaluable(r) for r in rows):
            return self._finding(FindingOutcome.UNKNOWN, detail)
        return self._finding(FindingOutcome.PASS, detail)

    @staticmethod
    def _finding(outcome: FindingOutcome, detail: str) -> ReasonFinding:
        return ReasonFinding(
            ReasonCode.NET_PNL_NOT_REAL,
            "cost-adjusted expectancy is positive and significant on the holdout",
            outcome,
            detail,
        )


class EdgeHoldsAcrossRegimes:
    """The edge must show in at least `min_regimes` regime segments of the holdout."""

    def __init__(self, thresholds: EvidenceThresholds) -> None:
        self._t = thresholds

    def assess(self, experiment: LedgerExperiment) -> ReasonFinding:
        by_segment: dict[tuple[str, str], list[EvidenceRow]] = {}
        for row in _holdout_rows(experiment, pooled=False):
            if row.segment is not None and self._t.evaluable(row):
                by_segment.setdefault(row.segment, []).append(row)
        passing = sum(1 for rows in by_segment.values() if all(self._t.shows_edge(r) for r in rows))
        detail = (
            f"{passing} of {len(by_segment)} evaluable regime segment(s) show the edge, "
            f"{self._t.min_regimes} needed"
        )
        if passing >= self._t.min_regimes:
            outcome = FindingOutcome.PASS
        elif len(by_segment) >= self._t.min_regimes:
            outcome = FindingOutcome.FAIL
        else:
            outcome = FindingOutcome.UNKNOWN
        return ReasonFinding(
            ReasonCode.TOO_FEW_REGIMES, "the edge holds across regimes", outcome, detail
        )


class LedgerOutcomeRule:
    def __init__(self, gates: Sequence[EvidenceGate]) -> None:
        if not gates:
            raise ValueError("an outcome rule needs at least one gate")
        self._gates = tuple(gates)

    @classmethod
    def standard(cls, thresholds: EvidenceThresholds | None = None) -> LedgerOutcomeRule:
        t = thresholds or EvidenceThresholds()
        return cls(
            [
                HoldoutEvaluated(),
                EnoughHoldoutObservations(t),
                HoldoutEdgeSignificant(t),
                EdgeHoldsAcrossRegimes(t),
            ]
        )

    def judge(self, experiment: LedgerExperiment) -> tuple[Verdict, tuple[ReasonFinding, ...]]:
        findings = tuple(gate.assess(experiment) for gate in self._gates)
        outcomes = {f.outcome for f in findings}
        if FindingOutcome.FAIL in outcomes:
            return Verdict.REJECTED, findings
        if FindingOutcome.UNKNOWN in outcomes:
            return Verdict.INCONCLUSIVE, findings
        return Verdict.VALIDATED, findings


class LedgerExperimentReportBuilder:
    """`ExperimentReportBuilder[LedgerExperiment]`, for every ledger-backed family."""

    def __init__(
        self, rule: LedgerOutcomeRule | None = None, minter: ExperimentIdMinter | None = None
    ) -> None:
        self._rule = rule or LedgerOutcomeRule.standard()
        self._minter = minter or ExperimentIdMinter()

    def build(
        self, source: LedgerExperiment, declaration: ExperimentDeclaration, versions: VersionStamp
    ) -> ExperimentReport:
        if declaration.family is not source.family:
            raise ValueError(
                f"a {source.family.value} study cannot be reported under a "
                f"{declaration.family.value} declaration"
            )
        verdict, reasons = self._rule.judge(source)
        outcome, reasons, notes = PredeclarationCap().apply(
            declaration, outcome_of(verdict), reasons, self._notes(source)
        )
        return ExperimentReport(
            experiment_id=self._minter.mint(declaration),
            declaration=declaration,
            versions=self._versions(versions, source),
            periods=self._periods(source.hypothesis),
            metrics=self._metrics(source),
            outcome=outcome,
            reasons=reasons,
            notes=notes,
            supporting={"evidence": self._evidence(source)},
        )

    @staticmethod
    def _periods(hypothesis: HypothesisDeclaration) -> ExperimentPeriods:
        holdout = DatePair(hypothesis.holdout_first, hypothesis.holdout_last)
        train_last = hypothesis.holdout_first - timedelta(days=1)
        train = (
            DatePair(hypothesis.study_first, train_last)
            if hypothesis.study_first <= train_last
            else None
        )
        return ExperimentPeriods(train=train, holdout=holdout)

    @staticmethod
    def _versions(versions: VersionStamp, source: LedgerExperiment) -> VersionStamp:
        if versions.cost_model is not None or len(source.cost_models) != 1:
            return versions
        return replace(versions, cost_model=CostModelVersion(source.cost_models[0], None, None))

    @staticmethod
    def _metrics(source: LedgerExperiment) -> ExperimentMetrics:
        pooled = _holdout_rows(source, pooled=True)
        if len(pooled) == 1:
            return ExperimentMetrics(expectancy=pooled[0].net_expectancy)
        return ExperimentMetrics()

    @staticmethod
    def _notes(source: LedgerExperiment) -> tuple[str, ...]:
        notes = [
            "no P&L: this is a feature study, so P&L, drawdown, Sharpe and PBO are not applicable",
            "expectancy is per observation after the cost model, in the study's own units",
        ]
        if len(_holdout_rows(source, pooled=True)) != 1:
            notes.append(
                "several pooled holdout trials exist (one per horizon or tail), so no single "
                "headline expectancy is stated; each is in the supporting evidence"
            )
        if source.dataset_versions:
            notes.append("dataset version(s): " + ", ".join(source.dataset_versions))
        if len(source.cost_models) > 1:
            notes.append("cost models: " + "; ".join(source.cost_models))
        return tuple(notes)

    @staticmethod
    def _evidence(source: LedgerExperiment) -> list[JsonValue]:
        return [
            {
                "role": r.role.value,
                "label": r.label,
                "regime_axis": r.regime_axis,
                "regime_label": r.regime_label,
                "sample_size": r.sample_size,
                "net_expectancy": None if r.net_expectancy is None else str(r.net_expectancy),
                "t_statistic": None if r.t_statistic is None else str(r.t_statistic),
            }
            for r in source.rows
        ]


class FeatureLedgerEvidence:
    @staticmethod
    def of(hypothesis: HypothesisDeclaration, trials: Sequence[FeatureTrial]) -> LedgerExperiment:
        mine = [t for t in trials if t.hypothesis_id == hypothesis.hypothesis_id]
        rows = tuple(
            EvidenceRow(
                t.role,
                t.regime_axis,
                t.regime_label,
                t.sample_size,
                t.cost_adjusted_expectancy,
                t.t_statistic,
                f"{t.feature_name}@{t.feature_version} {t.horizon_label}",
            )  # fmt: skip
            for t in mine
        )
        return _experiment(
            ExperimentFamily.FEATURE, hypothesis, rows,
            [t.dataset_version for t in mine], [t.cost_model for t in mine],
        )  # fmt: skip


class CrossSectionalLedgerEvidence:
    @staticmethod
    def of(
        hypothesis: HypothesisDeclaration, trials: Sequence[CrossSectionalTrial]
    ) -> LedgerExperiment:
        mine = [t for t in trials if t.hypothesis_id == hypothesis.hypothesis_id]
        rows = tuple(
            EvidenceRow(
                t.role,
                t.regime_axis,
                t.regime_label,
                t.sample_size,
                t.net_expectancy,
                t.t_statistic,
                f"{t.signal_horizon_label} -> {t.holding_horizon_label} {t.tail}",
            )  # fmt: skip
            for t in mine
        )
        return _experiment(
            ExperimentFamily.CROSS_SECTIONAL, hypothesis, rows,
            [t.dataset_version for t in mine], [t.cost_model for t in mine],
        )  # fmt: skip


class LeadLagLedgerEvidence:
    @staticmethod
    def of(hypothesis: HypothesisDeclaration, trials: Sequence[LeadLagTrial]) -> LedgerExperiment:
        mine = [t for t in trials if t.hypothesis_id == hypothesis.hypothesis_id]
        rows = tuple(
            EvidenceRow(
                t.role,
                t.regime_axis,
                t.regime_label,
                t.sample_size,
                t.cost_adjusted_expectancy,
                t.t_statistic,
                f"{t.predictor} -> {t.subject} {t.early_horizon_label}/{t.target_horizon_label} "
                f"{t.direction}",
            )  # fmt: skip
            for t in mine
        )
        return _experiment(
            ExperimentFamily.LEAD_LAG, hypothesis, rows,
            [t.dataset_version for t in mine], [t.cost_model for t in mine],
        )  # fmt: skip


def _experiment(
    family: ExperimentFamily,
    hypothesis: HypothesisDeclaration,
    rows: tuple[EvidenceRow, ...],
    dataset_versions: Sequence[str],
    cost_models: Sequence[str],
) -> LedgerExperiment:
    return LedgerExperiment(
        family,
        hypothesis,
        rows,
        tuple(sorted(set(dataset_versions))),
        tuple(sorted(set(cost_models))),
    )


class BackfilledDeclaration:
    """The declaration of a hypothesis that was recorded (id, feature, study and holdout dates)
    but never had its economic rationale written down. It says so instead of inventing one."""

    @staticmethod
    def of(family: ExperimentFamily, hypothesis: HypothesisDeclaration) -> ExperimentDeclaration:
        slug = re.sub(r"[^a-z0-9]+", "-", hypothesis.hypothesis_id.lower()).strip("-")
        return ExperimentDeclaration(
            family=family,
            slug=slug or "hypothesis",
            hypothesis=(
                f"hypothesis {hypothesis.hypothesis_id}: feature {hypothesis.feature_name} "
                f"version {hypothesis.feature_version}"
            ),
            economic_rationale=BACKFILLED_RATIONALE,
            falsification="backfilled: not recorded",
            parameter_grid={},
            feature_versions={hypothesis.feature_name: hypothesis.feature_version},
            declared_at=hypothesis.declared_at,
        )


class LedgerExperimentReader:
    """Reads one hypothesis and everything recorded against it from the three ledgers, through the
    same Protocols the studies write with, so a test can hand it in-memory ledgers."""

    def __init__(
        self,
        hypotheses: HypothesisRegistry,
        features: FeatureTrialLedger,
        cross_sectional: CrossSectionalTrialLedger,
        lead_lag: LeadLagTrialLedger,
    ) -> None:
        self._hypotheses = hypotheses
        self._features = features
        self._cross_sectional = cross_sectional
        self._lead_lag = lead_lag

    async def read(self, family: ExperimentFamily, hypothesis_id: str) -> LedgerExperiment:
        hypothesis = await self._hypotheses.get(hypothesis_id)
        if hypothesis is None:
            raise LookupError(f"no hypothesis {hypothesis_id!r} was declared")
        if family is ExperimentFamily.FEATURE:
            return FeatureLedgerEvidence.of(hypothesis, await self._features.all())
        if family is ExperimentFamily.CROSS_SECTIONAL:
            return CrossSectionalLedgerEvidence.of(hypothesis, await self._cross_sectional.all())
        if family is ExperimentFamily.LEAD_LAG:
            return LeadLagLedgerEvidence.of(hypothesis, await self._lead_lag.all())
        raise ValueError(f"{family.value} experiments are not backed by a trial ledger")
