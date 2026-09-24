"""From a curation result to the standard experiment report (EM-188).

`ExperimentReportBuilder` is the one seam every research family implements: a source of that
family's own shape in, the same `ExperimentReport` out, so reports compare across families.
`CurationExperimentReportBuilder` is the strategy family's: a walk-forward `CurationRecord`.

Nothing here judges. The outcome is the one the robustness verdict already reached (or, for a
record with no assessment, the curation criteria), mapped to the report's wording; the one thing
added is the holdout rule: a run that reserved no holdout can never be ACCEPTED, so the outcome is
capped at INCONCLUSIVE and the reason says why.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from emporos.backtest.curation import CurationRecord
from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.backtest.feed import FeedWindow
from emporos.backtest.metrics.decimal_math import ZERO
from emporos.backtest.provenance import ResearchProvenance
from emporos.backtest.robustness.assessment import RobustnessReport
from emporos.backtest.robustness.report import RobustnessDocument
from emporos.core.clock import IST
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import (
    BACKFILLED_NOT_PREDECLARED,
    DatasetVersion,
    DatePair,
    ExperimentDeclaration,
    ExperimentFamily,
    ExperimentMetrics,
    ExperimentOutcomeLabel,
    ExperimentPeriods,
    ExperimentReport,
    FindingOutcome,
    JsonValue,
    ReasonCode,
    ReasonFinding,
    RegimeMetrics,
    VersionStamp,
    WindowMetrics,
    outcome_of,
)
from emporos.domain.sizing import DeclaredSize, SizeSource

SourceT_contra = TypeVar("SourceT_contra", contravariant=True)


class ExperimentReportBuilder(Protocol[SourceT_contra]):
    """Builds the standard report from one family's own result."""

    def build(
        self, source: SourceT_contra, declaration: ExperimentDeclaration, versions: VersionStamp
    ) -> ExperimentReport: ...


class PredeclarationCap:
    """A report whose claim was reconstructed after the fact (a backfill) can only ever be
    REJECTED or INCONCLUSIVE: it is marked, and an ACCEPTED result is capped, never upgraded."""

    def apply(
        self,
        declaration: ExperimentDeclaration,
        outcome: ExperimentOutcomeLabel,
        reasons: tuple[ReasonFinding, ...],
        notes: tuple[str, ...],
    ) -> tuple[ExperimentOutcomeLabel, tuple[ReasonFinding, ...], tuple[str, ...]]:
        if declaration.is_predeclared:
            return outcome, reasons, notes
        notes = (BACKFILLED_NOT_PREDECLARED, *notes)
        if outcome is not ExperimentOutcomeLabel.ACCEPTED:
            return outcome, reasons, notes
        finding = ReasonFinding(
            ReasonCode.NOT_PREDECLARED,
            "the hypothesis was declared before the run",
            FindingOutcome.UNKNOWN,
            "the economic rationale was not recorded before the evidence was gathered",
        )
        return ExperimentOutcomeLabel.INCONCLUSIVE, (*reasons, finding), notes


class HoldoutRequirement:
    """A run that reserved no holdout can never be ACCEPTED: nothing shows the result was not tuned
    on every day it saw. It says so as a finding, and an ACCEPTED outcome is capped, never kept."""

    def apply(
        self, outcome: ExperimentOutcomeLabel, reasons: tuple[ReasonFinding, ...]
    ) -> tuple[ExperimentOutcomeLabel, tuple[ReasonFinding, ...]]:
        finding = ReasonFinding(
            ReasonCode.HOLDOUT_NOT_RESERVED,
            "a final holdout was reserved",
            FindingOutcome.UNKNOWN,
            "the run reserved no holdout, so nothing shows the result was not tuned on every day",
        )
        if outcome is ExperimentOutcomeLabel.ACCEPTED:
            outcome = ExperimentOutcomeLabel.INCONCLUSIVE
        return outcome, (*reasons, finding)


class RegimePooling:
    """Adds each regime's slices across walk-forward windows. Counts and net P&L add; a win rate
    is rebuilt from the whole wins each slice's rate stands for, so it is a real pooled rate, not
    a mean of rates."""

    def pool(
        self, slices: Iterable[tuple[str, int, Decimal, Decimal | None]]
    ) -> dict[str, RegimeMetrics]:
        counts: dict[str, int] = defaultdict(int)
        nets: dict[str, Decimal] = defaultdict(lambda: ZERO)
        wins: dict[str, int] = defaultdict(int)
        for regime, count, net_pnl, win_rate in slices:
            counts[regime] += count
            nets[regime] += net_pnl
            if win_rate is not None:
                wins[regime] += round(win_rate * count)
        return {
            regime: RegimeMetrics(
                counts[regime], nets[regime], Decimal(wins[regime]) / Decimal(counts[regime])
            )
            for regime in sorted(counts)
            if counts[regime] > 0
        }


class CalendarDays:
    """A `FeedWindow` (UTC instants, end exclusive) as the inclusive IST calendar days it covers."""

    @staticmethod
    def of(window: FeedWindow) -> DatePair:
        return CalendarDays.between(window.start, window.end)

    @staticmethod
    def between(start: datetime, end: datetime) -> DatePair:
        first = start.astimezone(IST).date()
        last = (end.astimezone(IST) - timedelta(microseconds=1)).date()
        return DatePair(first, max(first, last))


class CurationExperimentReportBuilder:
    def __init__(
        self, size: DeclaredSize, risk_limit: Decimal, minter: ExperimentIdMinter | None = None
    ) -> None:
        """`size` is the position value the run was judged at and `risk_limit` the platform's own
        max_position_value: a report always says what size it is about (EM-191 F4)."""
        self._size = size
        self._risk_limit = risk_limit
        self._minter = minter or ExperimentIdMinter()

    def build(
        self, source: CurationRecord, declaration: ExperimentDeclaration, versions: VersionStamp
    ) -> ExperimentReport:
        if declaration.family is not ExperimentFamily.STRATEGY:
            raise ValueError(f"a curation is a strategy experiment, not {declaration.family.value}")
        robustness = source.robustness
        periods = self._periods(robustness)
        outcome, reasons = self._verdict(source)
        notes: list[str] = []
        if periods.holdout is None:
            outcome, reasons = self._without_holdout(outcome, reasons)
        else:
            notes.append(
                "the final holdout was reserved before any window was planned and is not "
                "evaluated by curation: it stays untouched for a one-shot confirmation"
            )
        if source.costs is not None:
            notes.append(
                "the cost breakdown is the cost model's decomposition of each round trip at its "
                "own quantity and entry price; simulated slippage is already inside the fill "
                "prices, so gross P&L is after slippage and before charges"
            )
        notes.append(self._size_note())
        outcome, reasons, notes_ = PredeclarationCap().apply(
            declaration, outcome, reasons, tuple(notes)
        )
        return ExperimentReport(
            experiment_id=self._minter.mint(declaration),
            declaration=declaration,
            versions=self._versions(versions, source),
            periods=periods,
            metrics=self._metrics(source),
            outcome=outcome,
            reasons=reasons,
            notes=notes_,
            supporting=self._supporting(robustness),
        )

    def _size_note(self) -> str:
        size = self._size
        origin = (
            "declared before the run"
            if size.source is SizeSource.DECLARED
            else "not declared, so the risk limits' max_position_value"
        )
        note = f"judged at a position value of {size.position_value:,.2f} rupees ({origin})"
        if size.exceeds(self._risk_limit):
            note += (
                f"; that is above the platform's {self._risk_limit:,.2f}, so it cannot trade "
                "without an operator risk change (EDGE_SEARCH_PLAN §8)"
            )
        return note

    @staticmethod
    def _verdict(
        record: CurationRecord,
    ) -> tuple[ExperimentOutcomeLabel, tuple[ReasonFinding, ...]]:
        if record.robustness is not None:
            verdict = Verdict(record.robustness.verdict.verdict.value)
            reasons = tuple(
                ReasonFinding(g.code, g.name, FindingOutcome(g.outcome.value), g.detail)
                for g in record.robustness.verdict.gates
            )
            return outcome_of(verdict), reasons
        # No robustness assessment: the curation criteria stand in, and can never validate.
        reasons = tuple(
            ReasonFinding(
                None,
                c.name,
                FindingOutcome.PASS if c.passed else FindingOutcome.FAIL,
                f"{c.actual} (needs {c.required})",
            )
            for c in record.verdict.checks
        )
        verdict = Verdict.INCONCLUSIVE if record.verdict.passed else Verdict.REJECTED
        return outcome_of(verdict), reasons

    @staticmethod
    def _without_holdout(
        outcome: ExperimentOutcomeLabel, reasons: tuple[ReasonFinding, ...]
    ) -> tuple[ExperimentOutcomeLabel, tuple[ReasonFinding, ...]]:
        return HoldoutRequirement().apply(outcome, reasons)

    @staticmethod
    def _periods(robustness: RobustnessReport | None) -> ExperimentPeriods:
        if robustness is None:
            return ExperimentPeriods()
        provenance = robustness.provenance
        if provenance is not None:
            return ExperimentPeriods(
                train=CalendarDays.of(provenance.research),
                validation=CalendarDays.of(provenance.validation),
                holdout=CalendarDays.of(provenance.holdout),
            )
        windows = robustness.window_performance
        if not windows:
            return ExperimentPeriods()
        return ExperimentPeriods(
            validation=CalendarDays.between(
                min(w.test_start for w in windows), max(w.test_end for w in windows)
            )
        )

    @staticmethod
    def _versions(versions: VersionStamp, record: CurationRecord) -> VersionStamp:
        return replace(
            versions,
            behaviour_hash=versions.behaviour_hash or record.behaviour_hash,
            candidate_behaviour_hashes=versions.candidate_behaviour_hashes
            or dict(record.behaviour_hashes),
            dataset=versions.dataset or CurationExperimentReportBuilder._dataset(record.provenance),
        )

    @staticmethod
    def _dataset(provenance: ResearchProvenance | None) -> DatasetVersion | None:
        if provenance is None:
            return None
        return DatasetVersion(
            provenance.universe_hash,
            provenance.calendar_version,
            provenance.quarantine_hash,
            provenance.dataset_timeframe.value,
            provenance.dataset_first,
            provenance.dataset_last,
        )

    def _metrics(self, record: CurationRecord) -> ExperimentMetrics:
        stats = record.pooled.statistics
        robustness = record.robustness
        return ExperimentMetrics(
            gross_pnl=stats.gross_pnl.amount,
            net_pnl=stats.net_pnl.amount,
            expectancy=stats.expectancy,
            profit_factor=stats.profit_factor,
            max_drawdown=max(record.pooled.window_drawdowns, default=ZERO),
            sharpe=None if robustness is None else robustness.deflated_sharpe.annualised_sharpe,
            deflated_sharpe=None
            if robustness is None
            else robustness.deflated_sharpe.deflated_sharpe,
            pbo=None if robustness is None else robustness.pbo.probability_of_overfitting,
            trade_count=stats.count,
            win_rate=stats.win_rate,
            by_regime={} if robustness is None else self._regimes(robustness),
            by_window=() if robustness is None else self._windows(robustness),
            costs=record.costs,
        )

    @staticmethod
    def _regimes(robustness: RobustnessReport) -> dict[str, RegimeMetrics]:
        return RegimePooling().pool(
            (regime, slice_.count, slice_.net_pnl, slice_.win_rate)
            for window in robustness.window_performance
            for regime, slice_ in window.by_regime.items()
        )

    @staticmethod
    def _windows(robustness: RobustnessReport) -> tuple[WindowMetrics, ...]:
        found = []
        for w in robustness.window_performance:
            days = CalendarDays.between(w.test_start, w.test_end)
            found.append(WindowMetrics(w.index, days.first, days.last, w.net_pnl))
        return tuple(found)

    def _supporting(self, robustness: RobustnessReport | None) -> dict[str, JsonValue]:
        sizing: dict[str, JsonValue] = {
            "position_value": str(self._size.position_value),
            "source": self._size.source.value,
            "risk_limit": str(self._risk_limit),
            "requires_operator_risk_change": self._size.exceeds(self._risk_limit),
        }
        supporting: dict[str, JsonValue] = {"sizing": sizing}
        if robustness is not None:
            document: dict[str, Any] = RobustnessDocument().of(robustness)
            supporting["robustness"] = document
        return supporting
