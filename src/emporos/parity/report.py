"""The parity report as a JSON document (exact decimal strings) and as Markdown (EM-185).

Both are built from the same rows, so the numbers cannot differ between them. The Markdown has the
sections an operator reads in order: the headline verdict, the degradation table, then the
per-strategy, per-symbol and per-session rows, the per-trade ledger with every non-matched status
and its reason, and the latency distribution.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from emporos.domain.parity import ParityReport
from emporos.parity.ledger import SessionParity, SignalParity, TradeFacts
from emporos.parity.metrics import Delta, ParityAnalyzer, ParityMetrics
from emporos.parity.models import ParityStatus

Doc = dict[str, object]


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


class MetricsTable:
    """Every metric as {backtest, paper, absolute, relative} decimal strings."""

    def of(self, metrics: ParityMetrics) -> dict[str, dict[str, str | None]]:
        return {m.value: self._delta(d) for m, d in metrics.all().items()}

    @staticmethod
    def _delta(delta: Delta) -> dict[str, str | None]:
        return {
            "backtest": _s(delta.backtest), "paper": _s(delta.paper),
            "absolute": _s(delta.absolute), "relative": _s(delta.relative),
        }  # fmt: skip


class ParityDocument:
    def __init__(self, analyzer: ParityAnalyzer | None = None) -> None:
        self._analyzer = analyzer or ParityAnalyzer()
        self._table = MetricsTable()

    # --- JSON -------------------------------------------------------------------------------
    def to_json(self, report: ParityReport, sessions: Sequence[SessionParity]) -> Doc:
        sliced = self._analyzer.slices(sessions)
        return {
            "strategy": report.strategy,
            "behaviour_hash": report.behaviour_hash,
            "kind": report.kind.value,
            "period": report.period,
            "verdict": report.verdict.value,
            "gates": [
                {"name": g.name, "outcome": g.outcome, "detail": g.detail} for g in report.gates
            ],
            "sessions": report.sessions,
            "matched_trades": report.matched_trades,
            "signal_agreement": _s(sliced.overall.signal_agreement),
            "metrics": self._table.of(sliced.overall),
            "by_strategy": {k: self._table.of(v) for k, v in sliced.by_strategy.items()},
            "by_symbol": {k: self._table.of(v) for k, v in sliced.by_symbol.items()},
            "by_session": {k.isoformat(): self._table.of(v) for k, v in sliced.by_session.items()},
            "signals": [
                self._signal(s.session_date.isoformat(), r) for s in sessions for r in s.signals
            ],
            "recorded_at": report.recorded_at.isoformat(),
        }  # fmt: skip

    @staticmethod
    def _signal(day: str, row: SignalParity) -> Doc:
        return {
            "session": day, "instrument_id": row.instrument_id, "status": row.status.value,
            "reason": row.reason,
        }  # fmt: skip

    # --- Markdown ---------------------------------------------------------------------------
    def markdown(self, report: ParityReport, sessions: Sequence[SessionParity]) -> str:
        sliced = self._analyzer.slices(sessions)
        lines = [
            f"# Parity: {report.strategy} ({report.kind.value}, {report.period})",
            "",
            f"**Verdict: {report.verdict.value.upper()}** over {report.sessions} session(s), "
            f"{report.matched_trades} matched trade(s); signal agreement "
            f"{_show(sliced.overall.signal_agreement)}. Configuration `{report.behaviour_hash}`.",
            "",
            "## Gates",
            "",
            "| Gate | Outcome | Detail |",
            "| --- | --- | --- |",
            *[f"| {g.name} | {g.outcome.upper()} | {g.detail} |" for g in report.gates],
            "",
            "## Degradation (paper vs backtest)",
            "",
            *self._degradation(sliced.overall),
            "",
            "## By strategy",
            "",
            *self._slice_table("Strategy", sliced.by_strategy),
            "",
            "## By symbol",
            "",
            *self._slice_table("Symbol", sliced.by_symbol),
            "",
            "## By session",
            "",
            *self._slice_table("Session", {k.isoformat(): v for k, v in sliced.by_session.items()}),
            "",
            "## Signal ledger",
            "",
            "Every signal either side produced, with its status. Only MATCHED means the two agree.",
            "",
            "| Session | Instrument | Status | Reason |",
            "| --- | --- | --- | --- |",
            *[
                f"| {s.session_date.isoformat()} | {r.instrument_id} | {r.status.value} | "
                f"{r.reason} |"
                for s in sessions
                for r in s.signals
            ],
            "",
            "## Trade ledger",
            "",
            "| Session | Instrument | Direction | Paper net | Backtest net |",
            "| --- | --- | --- | --- | --- |",
            *[
                f"| {s.session_date.isoformat()} | {t.instrument_id} | {t.direction.value} | "
                f"{_net(t.paper)} | {_net(t.backtest)} |"
                for s in sessions
                for t in s.trades
            ],
            "",
            "## Latency (paper)",
            "",
            *self._latency(sessions),
            "",
            "## Status counts",
            "",
            *self._status_counts(sessions),
            "",
        ]
        return "\n".join(lines)

    def _degradation(self, m: ParityMetrics) -> list[str]:
        rows = [
            "| Metric | Backtest | Paper | Change | Relative |",
            "| --- | --- | --- | --- | --- |",
        ]
        for metric, delta in m.all().items():
            rows.append(
                f"| {metric.value} | {_show(delta.backtest)} | {_show(delta.paper)} | "
                f"{_show(delta.absolute)} | {_show(delta.relative)} |"
            )
        return rows

    def _slice_table(self, label: str, rows: dict[str, ParityMetrics]) -> list[str]:
        head = [
            f"| {label} | Fill rate (paper / backtest) | Net P&L (paper / backtest) | Trades |",
            "| --- | --- | --- | --- |",
        ]
        return head + [
            f"| {key} | {_show(m.paper.fill_rate)} / {_show(m.backtest.fill_rate)} | "
            f"{_show(m.paper.net_pnl)} / {_show(m.backtest.net_pnl)} | "
            f"{m.paper.trades} / {m.backtest.trades} |"
            for key, m in rows.items()
        ]

    @staticmethod
    def _latency(sessions: Sequence[SessionParity]) -> list[str]:
        columns: dict[str, list[timedelta]] = {"decision": [], "placement": [], "fill": []}
        reprices = 0
        for s in sessions:
            for r in s.signals:
                latency = None if r.paper_outcome is None else r.paper_outcome.latency
                if latency is None:
                    continue
                reprices += latency.reprices
                for name, value in (
                    ("decision", latency.decision), ("placement", latency.placement),
                    ("fill", latency.fill),
                ):  # fmt: skip
                    if value is not None:
                        columns[name].append(value)
        if not any(columns.values()):
            return ["No paper orders were timed."]
        rows = ["| Stage | Orders | Median (s) | Worst (s) |", "| --- | --- | --- | --- |"]
        for name, values in columns.items():
            if values:
                ordered = sorted(values)
                median = ordered[len(ordered) // 2].total_seconds()
                worst = ordered[-1].total_seconds()
                rows.append(f"| {name} | {len(ordered)} | {median:.3f} | {worst:.3f} |")
        return [*rows, "", f"Reprices: {reprices}."]

    @staticmethod
    def _status_counts(sessions: Sequence[SessionParity]) -> list[str]:
        counts = {status: 0 for status in ParityStatus}
        for s in sessions:
            for r in s.signals:
                counts[r.status] += 1
        return [f"- {status.value}: {n}" for status, n in counts.items()]


def _show(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return (
        format(value.quantize(Decimal("0.0001")), "f")
        if value != value.to_integral()
        else str(value)
    )


def _net(facts: TradeFacts | None) -> str:
    return "-" if facts is None else format(facts.net_pnl, "f")
