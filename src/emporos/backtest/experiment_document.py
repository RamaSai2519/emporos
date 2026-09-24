"""An experiment report as a JSON document and as Markdown (EM-188).

Both have a fixed section order and a fixed metric table, so every family renders the same rows and
a reader can lay two reports side by side: a metric that does not apply is "n/a", never omitted.
JSON carries every number exactly (decimals as strings, no float ever); Markdown rounds for humans.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from emporos.domain.research_experiments import (
    CostBreakdown,
    DatePair,
    ExperimentMetrics,
    ExperimentPeriods,
    ExperimentReport,
    ReasonFinding,
    VersionStamp,
)

NOT_APPLICABLE = "n/a"
_HUNDRED = Decimal(100)


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _money(value: Decimal | None) -> str:
    return NOT_APPLICABLE if value is None else f"{value:.2f}"


def _ratio(value: Decimal | None) -> str:
    return NOT_APPLICABLE if value is None else f"{value:.3f}"


def _percent(value: Decimal | None) -> str:
    return NOT_APPLICABLE if value is None else f"{value * _HUNDRED:.1f}%"


def _count(value: int | None) -> str:
    return NOT_APPLICABLE if value is None else str(value)


def _text(value: str | None) -> str:
    return value if value else NOT_APPLICABLE


def _span(period: DatePair | None) -> str:
    return NOT_APPLICABLE if period is None else f"{period.first} to {period.last}"


def _days(period: DatePair | None) -> dict[str, str] | None:
    return (
        None
        if period is None
        else {"first": period.first.isoformat(), "last": period.last.isoformat()}
    )


class ExperimentDocument:
    def to_json(self, report: ExperimentReport) -> dict[str, Any]:
        d = report.declaration
        return {
            "schema_version": report.schema_version,
            "experiment_id": str(report.experiment_id),
            "family": d.family.value,
            "slug": d.slug,
            "outcome": report.outcome.value,
            "declaration": {
                "predeclared": d.is_predeclared,
                "hypothesis": d.hypothesis,
                "economic_rationale": d.economic_rationale,
                "falsification": d.falsification,
                "parameter_grid": {k: list(v) for k, v in sorted(d.parameter_grid.items())},
                "feature_versions": dict(sorted(d.feature_versions.items())),
                "declared_at": d.declared_at.isoformat(),
                **({} if d.position_value is None else {"position_value": str(d.position_value)}),
            },
            "versions": self._versions(report.versions),
            "periods": {
                "train": _days(report.periods.train),
                "validation": _days(report.periods.validation),
                "holdout": _days(report.periods.holdout),
            },
            "metrics": self._metrics(report.metrics),
            "reasons": [self._reason(r) for r in report.reasons],
            "primary_reason_codes": [
                None if r.code is None else r.code.value for r in report.primary_reasons
            ],
            "notes": list(report.notes),
            "supporting": dict(report.supporting),
        }

    @staticmethod
    def _reason(reason: ReasonFinding) -> dict[str, str | None]:
        return {
            "code": None if reason.code is None else reason.code.value,
            "name": reason.name,
            "outcome": reason.outcome.value,
            "detail": reason.detail,
        }

    @staticmethod
    def _versions(v: VersionStamp) -> dict[str, Any]:
        dataset = v.dataset
        cost = v.cost_model
        return {
            "behaviour_hash": v.behaviour_hash,
            "candidate_behaviour_hashes": dict(sorted(v.candidate_behaviour_hashes.items())),
            "dataset": None
            if dataset is None
            else {
                "universe_hash": dataset.universe_hash,
                "calendar_version": dataset.calendar_version,
                "quarantine_hash": dataset.quarantine_hash,
                "timeframe": dataset.timeframe,
                "first": dataset.first.isoformat(),
                "last": dataset.last.isoformat(),
            },
            "cost_model": None
            if cost is None
            else {
                "fee_schedule_id": cost.fee_schedule_id,
                "slippage_bps": _s(cost.slippage_bps),
                "benchmark_hash": cost.benchmark_hash,
            },
            "code_revision": v.code_revision,
        }

    @staticmethod
    def _costs(costs: CostBreakdown | None) -> dict[str, str | None] | None:
        if costs is None:
            return None
        return {
            "brokerage": str(costs.brokerage),
            "statutory": str(costs.statutory),
            "spread": str(costs.spread),
            "slippage": str(costs.slippage),
            "total": str(costs.total),
            "per_trade_bps": _s(costs.per_trade_bps),
            "per_trade_inr": _s(costs.per_trade_inr),
        }

    def _metrics(self, m: ExperimentMetrics) -> dict[str, Any]:
        return {
            "gross_pnl": _s(m.gross_pnl),
            "net_pnl": _s(m.net_pnl),
            "expectancy": _s(m.expectancy),
            "profit_factor": _s(m.profit_factor),
            "max_drawdown": _s(m.max_drawdown),
            "sharpe": _s(m.sharpe),
            "deflated_sharpe": _s(m.deflated_sharpe),
            "pbo": _s(m.pbo),
            "trade_count": m.trade_count,
            "win_rate": _s(m.win_rate),
            "costs": self._costs(m.costs),
            "by_regime": {
                name: {"count": r.count, "net_pnl": str(r.net_pnl), "win_rate": _s(r.win_rate)}
                for name, r in sorted(m.by_regime.items())
            },
            "by_window": [
                {
                    "index": w.index,
                    "first": w.first.isoformat(),
                    "last": w.last.isoformat(),
                    "net_pnl": str(w.net_pnl),
                }
                for w in m.by_window
            ],
        }

    def markdown(self, report: ExperimentReport) -> str:
        d = report.declaration
        lines = [
            f"# {report.experiment_id}",
            "",
            f"**{report.outcome.value.upper()}** | {d.family.value} | {d.slug} | "
            f"declared {d.declared_at.isoformat()}",
            "",
            "## Declaration (made before the run)",
            "",
            f"- Pre-declared: {'yes' if d.is_predeclared else 'no (backfilled after the fact)'}",
            f"- Hypothesis: {d.hypothesis}",
            f"- Economic rationale: {d.economic_rationale}",
            f"- Falsified by: {d.falsification}",
        ]
        if d.position_value is not None:  # absent before F4: those reports render as they did
            lines.append(f"- Declared position value: ₹{d.position_value:,.2f}")
        lines += ["", "Parameter grid:", ""]
        lines += self._table(
            ("parameter", "values"),
            [(k, ", ".join(v)) for k, v in sorted(d.parameter_grid.items())],
        )
        lines += ["", "Feature versions:", ""]
        lines += self._table(("feature", "version"), sorted(d.feature_versions.items()))
        lines += ["", "## Versions", ""]
        lines += self._table(("what", "value"), self._version_rows(report.versions))
        lines += ["", "## Periods", ""]
        lines += self._table(("period", "days"), self._period_rows(report.periods))
        lines += ["", "## Headline metrics", ""]
        lines += self._table(("metric", "value"), self._metric_rows(report.metrics))
        lines += ["", "## Cost breakdown", ""]
        lines += self._table(("component", "amount"), self._cost_rows(report.metrics.costs))
        lines += ["", "## By regime", ""]
        lines += self._table(
            ("regime", "trades", "net P&L", "win rate"),
            [
                (name, str(r.count), _money(r.net_pnl), _percent(r.win_rate))
                for name, r in sorted(report.metrics.by_regime.items())
            ],
        )
        lines += ["", "## By walk-forward window", ""]
        lines += self._table(
            ("window", "first", "last", "net P&L"),
            [
                (str(w.index), w.first.isoformat(), w.last.isoformat(), _money(w.net_pnl))
                for w in report.metrics.by_window
            ],
        )
        lines += ["", "## Reasons", ""]
        lines += self._table(
            ("code", "rule", "outcome", "evidence"),
            [
                (
                    _text(None if r.code is None else r.code.value),
                    r.name,
                    r.outcome.value,
                    r.detail,
                )
                for r in report.reasons
            ],
        )
        lines += ["", "## Notes", ""]
        lines += [f"- {n}" for n in report.notes] or [f"- {NOT_APPLICABLE}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
        def cell(text: str) -> str:
            return text.replace("|", "\\|").replace("\n", " ")

        lines = [
            "| " + " | ".join(header) + " |",
            "|" + "---|" * len(header),
        ]
        body = rows or [[NOT_APPLICABLE] + [""] * (len(header) - 1)]
        return lines + ["| " + " | ".join(cell(c) for c in row) + " |" for row in body]

    @staticmethod
    def _version_rows(v: VersionStamp) -> list[tuple[str, str]]:
        dataset, cost = v.dataset, v.cost_model
        universe = quarantine = calendar = span = NOT_APPLICABLE
        if dataset is not None:
            universe, quarantine = dataset.universe_hash, dataset.quarantine_hash
            calendar = dataset.calendar_version
            span = f"{dataset.timeframe} {dataset.first} to {dataset.last}"
        schedule = slippage = benchmark = NOT_APPLICABLE
        if cost is not None:
            schedule, benchmark = _text(cost.fee_schedule_id), _text(cost.benchmark_hash)
            slippage = _text(_s(cost.slippage_bps))
        rows = [
            ("behaviour hash (base config)", _text(v.behaviour_hash)),
            ("dataset universe hash", universe),
            ("dataset calendar", calendar),
            ("dataset quarantine hash", quarantine),
            ("dataset span", span),
            ("fee schedule", schedule),
            ("slippage (bps per side)", slippage),
            ("benchmark hash", benchmark),
            ("code revision", _text(v.code_revision)),
        ]
        hashes = sorted(v.candidate_behaviour_hashes.items())
        return rows + [(f"candidate hash {name}", h) for name, h in hashes]

    @staticmethod
    def _period_rows(p: ExperimentPeriods) -> list[tuple[str, str]]:
        return [
            ("train", _span(p.train)),
            ("validation (walk-forward out-of-sample)", _span(p.validation)),
            ("holdout (reserved)", _span(p.holdout)),
        ]

    @staticmethod
    def _metric_rows(m: ExperimentMetrics) -> list[tuple[str, str]]:
        return [
            ("gross P&L", _money(m.gross_pnl)),
            ("net P&L", _money(m.net_pnl)),
            ("expectancy per trade", _money(m.expectancy)),
            ("profit factor", _ratio(m.profit_factor)),
            ("max drawdown (worst window)", _percent(m.max_drawdown)),
            ("Sharpe (annualised)", _ratio(m.sharpe)),
            ("Deflated Sharpe", _ratio(m.deflated_sharpe)),
            ("PBO", _ratio(m.pbo)),
            ("trades", _count(m.trade_count)),
            ("win rate", _percent(m.win_rate)),
        ]

    @staticmethod
    def _cost_rows(costs: CostBreakdown | None) -> list[tuple[str, str]]:
        c = costs
        return [
            ("brokerage", _money(None if c is None else c.brokerage)),
            ("statutory charges", _money(None if c is None else c.statutory)),
            ("spread", _money(None if c is None else c.spread)),
            ("slippage", _money(None if c is None else c.slippage)),
            ("total", _money(None if c is None else c.total)),
            ("per trade (bps of notional)", _ratio(None if c is None else c.per_trade_bps)),
            ("per trade (INR)", _money(None if c is None else c.per_trade_inr)),
        ]
