"""A robustness report as a JSON-able document and as Markdown: every number a reviewer needs to
check the verdict, nothing rounded away in the record (money and ratios are exact strings)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from emporos.backtest.robustness.assessment import RobustnessReport
from emporos.backtest.robustness.holdout import CurationProvenance
from emporos.backtest.robustness.monte_carlo import Interval
from emporos.backtest.robustness.performance import WindowPerformance
from emporos.backtest.robustness.verdict import DirectionStats


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _interval(interval: Interval | None) -> dict[str, str | None] | None:
    if interval is None:
        return None
    return {"observed": _s(interval.observed), "low": _s(interval.low), "high": _s(interval.high)}


class RobustnessDocument:
    def of(self, report: RobustnessReport) -> dict[str, Any]:
        mc, dsr, conc = report.monte_carlo, report.deflated_sharpe, report.concentration
        pbo = report.pbo
        distribution = mc.distribution
        return {
            "verdict": report.verdict.verdict.value,
            "gates": [
                {"name": g.name, "outcome": g.outcome.value, "detail": g.detail}
                for g in report.verdict.gates
            ],
            "monte_carlo": {
                "trades": mc.trade_count,
                "resamples": mc.resamples,
                "seed": mc.seed,
                "confidence": _s(mc.confidence),
                "inconclusive_reason": mc.inconclusive_reason,
                "net_pnl": None if distribution is None else _interval(distribution.net_pnl),
                "expectancy": None if distribution is None else _interval(distribution.expectancy),
                "profit_factor": None
                if distribution is None
                else _interval(distribution.profit_factor),
                "max_drawdown": None
                if distribution is None
                else _interval(distribution.max_drawdown),
                "probability_net_positive": None
                if distribution is None
                else _s(distribution.probability_net_positive),
                "probability_of_ruin": None
                if distribution is None
                else _s(distribution.probability_of_ruin),
            },
            "deflated_sharpe": {
                "observations": dsr.observations,
                "trial_count": dsr.trial_count,
                "daily_sharpe": _s(dsr.daily_sharpe),
                "annualised_sharpe": _s(dsr.annualised_sharpe),
                "expected_max_sharpe": _s(dsr.expected_max_sharpe),
                "probabilistic_sharpe": _s(dsr.probabilistic_sharpe),
                "deflated_sharpe": _s(dsr.deflated_sharpe),
                "reason": dsr.reason,
            },
            "pbo": {
                "candidate_count": pbo.candidate_count,
                "block_count": pbo.block_count,
                "combination_count": pbo.combination_count,
                "probability_of_overfitting": _s(pbo.probability_of_overfitting),
                "mean_logit": _s(pbo.mean_logit),
                "reason": pbo.reason,
            },
            "portfolio_economics": {
                "observed_edge_bps": _s(report.evidence.observed_edge_bps),
                "minimum_edge_bps": _s(report.evidence.minimum_edge_bps),
            },
            "concentration": {
                "top_instrument": conc.top_instrument,
                "top_instrument_share": _s(conc.top_instrument_share),
                "top_month": conc.top_month,
                "top_month_share": _s(conc.top_month_share),
                "top_trades": conc.top_trades,
                "top_trades_share": _s(conc.top_trades_share),
            },
            "cost_scenarios": [
                {
                    "name": c.name,
                    "fee_multiplier": str(c.fee_multiplier),
                    "extra_slippage_bps": str(c.extra_slippage_bps),
                    "net_pnl": str(c.net_pnl),
                }
                for c in report.costs
            ],
            "perturbation": None
            if report.perturbation is None
            else {
                "profitable_share": _s(report.perturbation.profitable_share),
                "runs": [
                    {"window": r.window, "candidate": r.candidate, "net_pnl": str(r.net_pnl)}
                    for r in report.perturbation.runs
                ],
            },
            "baseline_net_pnl": _s(report.baseline_net_pnl),
            "direction": self._direction(report.evidence.direction),
            "windows": [self._window(w) for w in report.window_performance],
            "provenance": self._provenance(report.provenance),
        }

    @staticmethod
    def _direction(direction: DirectionStats) -> dict[str, Any]:
        return {
            "long": {
                "trades": direction.long_count,
                "net_pnl": _s(direction.long_net_pnl),
            },
            "short": {
                "trades": direction.short_count,
                "net_pnl": _s(direction.short_net_pnl),
            },
        }

    @staticmethod
    def _window(window: WindowPerformance) -> dict[str, Any]:
        return {
            "index": window.index,
            "test_start": window.test_start.isoformat(),
            "test_end": window.test_end.isoformat(),
            "net_pnl": _s(window.net_pnl),
            "by_regime": {
                regime: {
                    "count": slice_.count,
                    "net_pnl": _s(slice_.net_pnl),
                    "win_rate": _s(slice_.win_rate),
                }
                for regime, slice_ in window.by_regime.items()
            },
        }

    @staticmethod
    def _provenance(provenance: CurationProvenance | None) -> dict[str, Any] | None:
        if provenance is None:
            return None
        return {
            "research": {
                "first": provenance.research.start.isoformat(),
                "last": provenance.research.end.isoformat(),
            },
            "validation": {
                "first": provenance.validation.start.isoformat(),
                "last": provenance.validation.end.isoformat(),
            },
            "holdout": {
                "first": provenance.holdout.start.isoformat(),
                "last": provenance.holdout.end.isoformat(),
            },
        }

    def markdown(self, report: RobustnessReport) -> list[str]:
        direction = report.evidence.direction
        lines = [
            f"Classification: **{report.verdict.verdict.value.upper()}**",
            "",
            "| gate | outcome | evidence |",
            "|---|---|---|",
        ]
        lines += [f"| {g.name} | {g.outcome.value} | {g.detail} |" for g in report.verdict.gates]
        lines += [
            "",
            f"By direction (out-of-sample, {report.evidence.trade_count} trades total):",
            "",
            "| side | trades | net P&L |",
            "|---|---|---|",
            f"| long | {direction.long_count} | {direction.long_net_pnl:.2f} |",
            f"| short | {direction.short_count} | {direction.short_net_pnl:.2f} |",
            "",
            "Net P&L if costs were different (first-order re-pricing of the same trades):",
            "",
        ]
        lines += [f"- {c.name}: {c.net_pnl:.2f}" for c in report.costs]
        return lines
