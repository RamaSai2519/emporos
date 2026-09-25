"""The plain-text report of the A4 book (EM-233).

The common report (`format_report`) prints the arms against the §3.2 bar. This adds what a book of
sleeves owes a reader: the header that states its limits, each sleeve alone, the sleeves' monthly
correlation (overall and in the equity sleeve's worst 12 months), the quarterly resets, each ETF's
share of net profit, and the sub-period from 2018-01-01 beside the declared window.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from emporos.research.swing.book_runner import BookReport
from emporos.research.swing.combine import MonthlyCorrelation
from emporos.research.swing.metrics import SwingMetrics
from emporos.research.swing.report import format_report, instrument_share_lines, sub_period_lines

__all__ = ["format_book_report"]

LIMITS = (
    "A4 is built at SLEEVE level and says so: each sleeve is simulated on its share of the "
    "book's capital and only its return series is carried into the book, so after a quarterly "
    "reset the sleeve's whole-share rounding and position sizes are those of the run, not of the "
    "new capital. A reset is made at the previous close and costs the round trip of a Rs 25,000 "
    "reference order (fees and slippage of the scenario) on the rupees moved, unrounded. The "
    "§3.5 month halt and kill act on the EQUITY SLEEVE's own P&L, never on the book's; the "
    "equity sleeve's cash earns nothing (as in A1b), the defensive sleeve's earns 5% a year "
    "(as in A5)."
)
CONCENTRATION_NOTE = (
    "Concentration (§3.2 as amended): the 25% limit applies to single stocks. The broad index ETFs "
    "are exempt from the check; each ETF's share of net profit is reported below."
)


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _corr(c: MonthlyCorrelation) -> list[str]:
    months = ", ".join(f"{y}-{m:02d}" for y, m in c.worst_months)
    overall = "n/a" if c.overall is None else f"{c.overall:+.2f}"
    worst = "n/a" if c.in_worst is None else f"{c.in_worst:+.2f}"
    return [
        f"  monthly correlation, equity vs defensive sleeve: overall {overall} over {c.months} "
        f"months; in the equity sleeve's worst {len(c.worst_months)} months {worst} "
        f"(n = {len(c.worst_months)}: read it as a sign, not a number)",
        f"  in those months the equity sleeve averaged {_pct(c.first_mean_in_worst)} and the "
        f"defensive sleeve {_pct(c.second_mean_in_worst)}: {months}",
    ]


def format_book_report(
    report: BookReport, etf_names: Mapping[str, str], sub_start: date
) -> list[str]:
    lines = [LIMITS, "", CONCENTRATION_NOTE, ""]
    lines += format_report(report.cell)
    lines += [
        "",
        "the sleeves alone (benchmark costs, on their share of the capital), and the reset:",
    ]
    for arm in report.arms:
        lines.append(f"split {arm.label} (equity/defensive):")
        for sleeve in arm.sleeve_runs:
            s = SwingMetrics.of(sleeve.run)
            sharpe = "n/a" if s.net_sharpe is None else f"{s.net_sharpe:.2f}"
            lines.append(
                f"  {sleeve.name:<10} CAGR {_pct(s.net_cagr)}, Sharpe {sharpe}, worst month "
                f"{_pct(s.worst_month)}, max drawdown {_pct(s.max_drawdown)}, months with "
                f"exposure positive {_pct(s.positive_month_share_exposed, 0)}, all months "
                f"negative {_pct(s.negative_month_share, 0)}, {s.round_trips} round trips"
            )
        lines += _corr(arm.correlation)
        moved = sum((t.moved for t in arm.book.transfers), Decimal(0))
        lines.append(
            f"  {len(arm.book.transfers)} quarterly resets moved Rs {moved:,.0f} and cost "
            f"Rs {arm.book.transfer_cost:,.0f} at benchmark costs; sessions a sleeve had that the "
            f"other did not (left out of the book): {arm.book.sleeve_days_dropped}"
        )
    lines += instrument_share_lines(
        [(f"split {a.label}", a.outcome.arm) for a in report.arms],
        dict(etf_names),
        "each ETF's share of the book's net trade profit (exempt from the 25% check):",
    )
    runs = [(f"split {a.label}", a.outcome.arm) for a in report.arms]
    benchmark = [(f"BENCHMARK blend {a.label}", a.benchmark) for a in report.arms]
    lines += sub_period_lines([*runs, *benchmark], sub_start)
    return lines
