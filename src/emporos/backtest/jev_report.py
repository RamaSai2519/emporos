"""The Jev incremental experiment as a published `ExperimentReport` (EM-187, family
`JEV_INCREMENTAL`), on EM-188's registry.

The headline metrics are the Jev arm of the headline variant; the baseline arm, every variant's
deltas, Jev's cost in rupees, the paired-bootstrap interval, the per-regime deltas and the
provenance (model, knowledge cutoff and its source, prompt version and hash, provider mode) are
carried in `supporting` (JSON) and summarised in the notes (markdown).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.backtest.experiment_report import HoldoutRequirement, PredeclarationCap
from emporos.backtest.jev_incremental import ArmMetrics, JevIncrementalEvidence
from emporos.backtest.jev_sweep import JevSweepOutcome, JevVariantOutcome
from emporos.domain.research_experiments import (
    DatePair,
    ExperimentDeclaration,
    ExperimentFamily,
    ExperimentMetrics,
    ExperimentPeriods,
    ExperimentReport,
    FindingOutcome,
    JsonValue,
    ReasonFinding,
    RegimeMetrics,
    VersionStamp,
    outcome_of,
)


@dataclass(frozen=True)
class JevProvenance:
    model: str
    knowledge_cutoff: str  # ISO date, as declared
    cutoff_source: str
    cutoff_margin_days: int
    prompt_version: str
    prompt_hash: str
    provider_mode: str  # "record" or "replay"
    anonymised: bool


@dataclass(frozen=True)
class JevReportSource:
    outcome: JevSweepOutcome
    provenance: JevProvenance
    window: DatePair
    holdout: DatePair | None
    baseline_standings: Mapping[str, str]  # strategy -> its standing today


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class JevExperimentReportBuilder:
    def __init__(self, minter: ExperimentIdMinter | None = None) -> None:
        self._minter = minter or ExperimentIdMinter()

    def build(
        self, source: JevReportSource, declaration: ExperimentDeclaration, versions: VersionStamp
    ) -> ExperimentReport:
        if declaration.family is not ExperimentFamily.JEV_INCREMENTAL:
            raise ValueError(
                f"a Jev sweep is a jev_incremental experiment, not {declaration.family.value}"
            )
        headline = source.outcome.headline
        outcome = outcome_of(headline.verdict.verdict)
        reasons = tuple(
            ReasonFinding(g.code, g.name, FindingOutcome(g.outcome.value), g.detail)
            for g in headline.verdict.gates
        )
        if source.holdout is None:
            outcome, reasons = HoldoutRequirement().apply(outcome, reasons)
        outcome, reasons, notes = PredeclarationCap().apply(
            declaration, outcome, reasons, self._notes(source, headline)
        )
        return ExperimentReport(
            experiment_id=self._minter.mint(declaration),
            declaration=declaration,
            versions=versions,
            periods=ExperimentPeriods(validation=source.window, holdout=source.holdout),
            metrics=self._metrics(headline),
            outcome=outcome,
            reasons=reasons,
            notes=notes,
            supporting=self._supporting(source, headline),
        )

    @staticmethod
    def _metrics(headline: JevVariantOutcome) -> ExperimentMetrics:
        metrics = headline.comparison.treatment.metrics
        arm, stats = headline.evidence.treatment, metrics.trades
        return ExperimentMetrics(
            gross_pnl=stats.gross_pnl.amount,
            net_pnl=arm.net_pnl,
            expectancy=arm.expectancy,
            profit_factor=stats.profit_factor,
            max_drawdown=arm.max_drawdown,
            sharpe=arm.sharpe,
            deflated_sharpe=arm.deflated_sharpe,
            pbo=None,
            trade_count=arm.trade_count,
            win_rate=stats.win_rate,
            by_regime={
                name: RegimeMetrics(s.count, s.net_pnl.amount, s.win_rate)
                for name, s in sorted(metrics.by_regime.items())
                if s.count > 0
            },
        )

    def _notes(self, source: JevReportSource, headline: JevVariantOutcome) -> tuple[str, ...]:
        p, e = source.provenance, headline.evidence
        candidates = [v.variant.candidate_label(p.prompt_version) for v in source.outcome.variants]
        return (
            "the headline metrics are the Jev arm; the baseline (Jev off, otherwise identical) "
            f"has {e.baseline.trade_count} trades, net {e.baseline.net_pnl:.2f}, "
            f"max drawdown {e.baseline.max_drawdown:.4f}; the Jev arm's deltas are net "
            f"{e.deltas.net_pnl:+.2f}, trades {e.deltas.trade_count:+d}, drawdown "
            f"{e.deltas.max_drawdown:+.4f}",
            f"Jev cost {e.cost.inr:.2f} INR ({e.cost.tokens} tokens over {e.cost.reviews} "
            f"reviews, {e.cost.rejections} rejections); net of that cost the gain is "
            f"{e.net_of_jev_pnl_delta:+.2f} INR",
            f"model {p.model}, declared knowledge cutoff {p.knowledge_cutoff} "
            f"(source: {p.cutoff_source}; {p.cutoff_margin_days}-day margin), prompt "
            f"{p.prompt_version} ({p.prompt_hash[:12]}), provider {p.provider_mode}, "
            f"symbols {'anonymised' if p.anonymised else 'NOT anonymised'}",
            f"{len(candidates)} variant(s) tried and all recorded as trials "
            f"({source.outcome.trial_count} trials in the ledger when judged): "
            f"{', '.join(candidates)}. The headline is the best by net-of-Jev gain per trade, "
            "so it needs the reserved holdout to confirm",
            "the Jev arm was not judged by the full walk-forward policy, so this experiment "
            "cannot be ACCEPTED until it is",
        )

    def _supporting(
        self, source: JevReportSource, headline: JevVariantOutcome
    ) -> dict[str, JsonValue]:
        p = source.provenance
        return {
            "provenance": {
                "model": p.model,
                "knowledge_cutoff": p.knowledge_cutoff,
                "cutoff_source": p.cutoff_source,
                "cutoff_margin_days": p.cutoff_margin_days,
                "prompt_version": p.prompt_version,
                "prompt_hash": p.prompt_hash,
                "provider_mode": p.provider_mode,
                "anonymised": p.anonymised,
                "fingerprint": source.outcome.fingerprint.digest,
            },
            "baseline_standings": dict(sorted(source.baseline_standings.items())),
            "headline": headline.variant.candidate_label(p.prompt_version),
            "baseline": self._arm(headline.evidence.baseline),
            "variants": [self._variant(v, p.prompt_version) for v in source.outcome.variants],
        }

    def _variant(self, v: JevVariantOutcome, prompt_version: str) -> dict[str, JsonValue]:
        e = v.evidence
        return {
            "candidate": v.variant.candidate_label(prompt_version),
            "verdict": v.verdict.verdict.value,
            "treatment": self._arm(e.treatment),
            "deltas": {
                "net_pnl": str(e.deltas.net_pnl),
                "trade_count": e.deltas.trade_count,
                "expectancy": _s(e.deltas.expectancy),
                "sharpe": _s(e.deltas.sharpe),
                "deflated_sharpe": _s(e.deltas.deflated_sharpe),
                "max_drawdown": str(e.deltas.max_drawdown),
                "turnover": _s(e.deltas.turnover),
                "charges": str(e.deltas.charges),
                "net_of_jev_pnl": str(e.net_of_jev_pnl_delta),
                "net_of_jev_average_trade": _s(e.net_of_jev_average_trade_delta),
            },
            "jev_cost": {
                "reviews": e.cost.reviews,
                "rejections": e.cost.rejections,
                "tokens": e.cost.tokens,
                "latency_ms": e.cost.latency_ms,
                "inr_per_1k_tokens": str(e.cost.inr_per_1k_tokens),
                "inr": str(e.cost.inr),
            },
            "paired_bootstrap": self._paired(e),
            "by_regime": {
                name: {
                    "baseline_count": d.baseline_count,
                    "treatment_count": d.treatment_count,
                    "net_pnl_delta": str(d.net_pnl_delta),
                    "expectancy_delta": _s(d.expectancy_delta),
                }
                for name, d in sorted(e.by_regime.items())
            },
            "requests": v.tally.requests,
        }

    @staticmethod
    def _paired(e: JevIncrementalEvidence) -> dict[str, JsonValue] | None:
        if e.paired is None:
            return {"unavailable": e.paired_reason}
        mean, sharpe = e.paired.mean_daily_return, e.paired.daily_sharpe
        return {
            "days": e.paired.days,
            "resamples": e.paired.resamples,
            "seed": e.paired.seed,
            "confidence": str(e.paired.confidence),
            "mean_daily_return": {
                "observed": _s(mean.observed),
                "low": str(mean.low),
                "high": str(mean.high),
            },
            "daily_sharpe": None
            if sharpe is None
            else {
                "observed": _s(sharpe.observed),
                "low": str(sharpe.low),
                "high": str(sharpe.high),
            },
        }

    @staticmethod
    def _arm(a: ArmMetrics) -> dict[str, JsonValue]:
        return {
            "net_pnl": str(a.net_pnl),
            "trade_count": a.trade_count,
            "expectancy": _s(a.expectancy),
            "average_trade": _s(a.average_trade),
            "sharpe": _s(a.sharpe),
            "deflated_sharpe": _s(a.deflated_sharpe),
            "max_drawdown": str(a.max_drawdown),
            "turnover": _s(a.turnover),
            "charges": str(a.charges),
        }
