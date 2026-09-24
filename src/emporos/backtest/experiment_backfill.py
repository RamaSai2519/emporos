"""Turns a curation report published before experiment reports existed into one (EM-188).

Nothing is fabricated. Those reports carry no economic rationale, no falsification test, no
versions and (mostly) no periods beyond their walk-forward windows, so the report says exactly
that: the rationale reads "backfilled: not pre-declared", every field the source did not record is
None, the BACKFILLED_NOT_PREDECLARED note is present, and the outcome is the one the source
recorded, never upgraded (a backfilled report can only be REJECTED or INCONCLUSIVE).

Field access mirrors `verdict_records.ReportVerdicts`: the same JSON, read the same way.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.backtest.experiment_report import CalendarDays, PredeclarationCap, RegimePooling
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import (
    BACKFILLED_RATIONALE,
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


@dataclass(frozen=True)
class BackfillSource:
    """One strategy's entry of a published curation JSON, and where it came from."""

    document: Mapping[str, Any]
    tag: str  # which published run it is, e.g. "benchmark-50k"; makes the slug unique
    path: str  # the file it was read from, as the report should cite it
    first_committed: datetime  # when the file entered the repository: the only date on record


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class ReportBackfill:
    def __init__(
        self, gate_codes: Mapping[str, ReasonCode], minter: ExperimentIdMinter | None = None
    ) -> None:
        """`gate_codes` maps a gate's name to its code, for reports written before gates had
        codes (the standard policy's own gates are the source of truth)."""
        self._gate_codes = gate_codes
        self._minter = minter or ExperimentIdMinter()

    def declaration(self, source: BackfillSource) -> ExperimentDeclaration:
        strategy = str(source.document["strategy"])
        slug = re.sub(r"[^a-z0-9]+", "-", f"{strategy}-{source.tag}".lower()).strip("-")
        return ExperimentDeclaration(
            family=ExperimentFamily.STRATEGY,
            slug=slug,
            hypothesis=f"backfilled from {source.path}: {strategy} curated walk-forward",
            economic_rationale=BACKFILLED_RATIONALE,
            falsification="backfilled: not recorded",
            parameter_grid={"candidate": tuple(str(c) for c in source.document["candidates"])},
            feature_versions={},
            declared_at=source.first_committed,
        )

    def build(
        self, source: BackfillSource, declaration: ExperimentDeclaration, versions: VersionStamp
    ) -> ExperimentReport:
        document = source.document
        robustness = document.get("robustness")
        outcome, reasons = self._verdict(document, robustness)
        notes: tuple[str, ...] = (
            f"backfilled from {source.path}, first committed {source.first_committed.date()}: "
            "the run predates experiment reports, so what it did not record is n/a",
        )
        outcome, reasons, notes = PredeclarationCap().apply(declaration, outcome, reasons, notes)
        return ExperimentReport(
            experiment_id=self._minter.mint(declaration),
            declaration=declaration,
            versions=versions,
            periods=self._periods(document, robustness),
            metrics=self._metrics(document, robustness),
            outcome=outcome,
            reasons=reasons,
            notes=notes,
            supporting=self._supporting(source, robustness),
        )

    def _verdict(
        self, document: Mapping[str, Any], robustness: Mapping[str, Any] | None
    ) -> tuple[ExperimentOutcomeLabel, tuple[ReasonFinding, ...]]:
        if robustness is not None:
            reasons = tuple(
                ReasonFinding(self._code(g), g["name"], FindingOutcome(g["outcome"]), g["detail"])
                for g in robustness["gates"]
            )
            return outcome_of(Verdict(robustness["verdict"])), reasons
        verdict = Verdict.INCONCLUSIVE if document["passed"] else Verdict.REJECTED
        reasons = tuple(
            ReasonFinding(
                None,
                c["name"],
                FindingOutcome.PASS if c["passed"] else FindingOutcome.FAIL,
                f"{c['actual']} (needs {c['required']})",
            )
            for c in document["checks"]
        )
        return outcome_of(verdict), reasons

    def _code(self, gate: Mapping[str, Any]) -> ReasonCode | None:
        recorded = gate.get("code")
        return ReasonCode(recorded) if recorded else self._gate_codes.get(str(gate["name"]))

    @staticmethod
    def _periods(
        document: Mapping[str, Any], robustness: Mapping[str, Any] | None
    ) -> ExperimentPeriods:
        provenance = None if robustness is None else robustness.get("provenance")
        if provenance is not None:

            def span(key: str) -> DatePair:
                return DatePair(
                    date.fromisoformat(provenance[key]["first"]),
                    date.fromisoformat(provenance[key]["last"]),
                )

            return ExperimentPeriods(span("research"), span("validation"), span("holdout"))
        windows = document.get("windows") or []
        if not windows:
            return ExperimentPeriods()
        first = min(date.fromisoformat(w[0]) for w in windows)
        last = max(date.fromisoformat(w[1]) for w in windows)
        return ExperimentPeriods(validation=DatePair(first, max(first, last)))

    def _metrics(
        self, document: Mapping[str, Any], robustness: Mapping[str, Any] | None
    ) -> ExperimentMetrics:
        oos = document["out_of_sample"]
        dsr = {} if robustness is None else robustness.get("deflated_sharpe") or {}
        pbo = {} if robustness is None else robustness.get("pbo") or {}
        drawdowns = [Decimal(d) for d in oos.get("window_max_drawdowns") or []]
        return ExperimentMetrics(
            gross_pnl=_decimal(oos.get("gross_pnl")),
            net_pnl=_decimal(oos.get("net_pnl")),
            expectancy=_decimal(oos.get("expectancy")),
            profit_factor=_decimal(oos.get("profit_factor")),
            max_drawdown=max(drawdowns) if drawdowns else None,
            sharpe=_decimal(oos.get("sharpe") or dsr.get("annualised_sharpe")),
            deflated_sharpe=_decimal(dsr.get("deflated_sharpe")),
            pbo=_decimal(pbo.get("probability_of_overfitting")),
            trade_count=oos.get("trades"),
            win_rate=_decimal(oos.get("win_rate")),
            by_regime=self._regimes(robustness),
            by_window=self._windows(document, robustness),
        )

    @staticmethod
    def _regimes(robustness: Mapping[str, Any] | None) -> dict[str, RegimeMetrics]:
        windows = [] if robustness is None else robustness.get("windows") or []
        return RegimePooling().pool(
            (regime, int(s["count"]), Decimal(s["net_pnl"]), _decimal(s.get("win_rate")))
            for w in windows
            for regime, s in (w.get("by_regime") or {}).items()
        )

    @classmethod
    def _windows(
        cls, document: Mapping[str, Any], robustness: Mapping[str, Any] | None
    ) -> tuple[WindowMetrics, ...]:
        recorded = [] if robustness is None else robustness.get("windows") or []
        if recorded:
            return tuple(cls._recorded_window(w) for w in recorded)
        windows = document.get("windows") or []
        nets = document["out_of_sample"].get("window_nets") or []
        if len(windows) != len(nets):
            return ()
        return tuple(
            cls._listed_window(index, w, net)
            for index, (w, net) in enumerate(zip(windows, nets, strict=True))
        )

    @staticmethod
    def _recorded_window(window: Mapping[str, Any]) -> WindowMetrics:
        days = CalendarDays.between(
            datetime.fromisoformat(window["test_start"]), datetime.fromisoformat(window["test_end"])
        )
        return WindowMetrics(
            int(window["index"]), days.first, days.last, Decimal(window["net_pnl"])
        )

    @staticmethod
    def _listed_window(index: int, window: list[str], net: str) -> WindowMetrics:
        first = date.fromisoformat(window[0])
        return WindowMetrics(index, first, max(first, date.fromisoformat(window[1])), Decimal(net))

    @staticmethod
    def _supporting(
        source: BackfillSource, robustness: Mapping[str, Any] | None
    ) -> dict[str, JsonValue]:
        supporting: dict[str, JsonValue] = {"source": source.path}
        if robustness is not None:
            supporting["robustness"] = dict(robustness)
        return supporting
