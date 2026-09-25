"""The C1 core screen's own tables: every number the §10 bar reads, arm against benchmark."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from emporos.research.swing.core_screen import HALF_SPLIT, CoreMeasures
from emporos.research.swing.metrics import SwingStats
from emporos.research.swing.screen import SwingVerdict

__all__ = ["CoreRow", "core_lines"]


@dataclass(frozen=True)
class CoreRow:
    label: str
    measures: CoreMeasures
    verdict: SwingVerdict


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _line(label: str, s: SwingStats, first: SwingStats, second: SwingStats) -> str:
    return (
        f"{label:<24} {_pct(s.net_cagr):>7} {_pct(first.net_cagr):>7} {_pct(second.net_cagr):>7} "
        f"{_pct(s.max_drawdown):>7} {_pct(s.worst_month):>7} "
        f"{_pct(s.positive_month_share_exposed, 0):>6} {_pct(s.negative_month_share, 0):>6} "
        f"{_num(s.monthly_t):>5} {_num(s.net_sharpe):>6}"
    )


def core_lines(rows: Sequence[CoreRow], benchmark_label: str) -> list[str]:
    first = rows[0].measures
    lines = [
        f"PROFIT_PLAN §10 core bar (benchmark costs; halves split at {HALF_SPLIT}):",
        f"{'':<24} {'CAGR':>7} {'<=2016':>7} {'2017+':>7} {'maxDD':>7} {'worst':>7} "
        f"{'m+exp':>6} {'m-all':>6} {'t':>5} {'Sharpe':>6}",
        _line(
            f"BENCH {benchmark_label[:17]}",
            first.benchmark,
            first.benchmark_first_half,
            first.benchmark_second_half,
        ),
    ]
    for row in rows:
        m = row.measures
        lines.append(_line(row.label, m.stats, m.first_half, m.second_half))
    lines += [
        "",
        "the rest of the bar (adverse-cost CAGR, §3.4, drawdown against 0.6 x benchmark):",
    ]
    for row in rows:
        m = row.measures
        lines.append(
            f"{row.label:<24} adverse CAGR {_pct(m.adverse_cagr)}  P(drawdown>=30%) "
            f"{_pct(m.p_drawdown_30)}  maxDD {_pct(m.stats.max_drawdown)} vs limit "
            f"{_pct(0.6 * m.benchmark.max_drawdown)}  ->  "
            f"{'PASS' if row.verdict.passed else 'reject'}: "
            f"{'; '.join(row.verdict.failed_checks) or 'passed every check'}"
        )
    return lines
