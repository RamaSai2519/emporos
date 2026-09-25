"""The plain-text report of a screened cell (EM-228, EM-229).

It carries what the head asked every Track A report to carry: the effective start, days in cash,
round trips, net per calendar year, the same-universe benchmark line, the §3.4 numbers for the
concentrated arms, and (A2) the reaction sessions used per year (the cell's own notes)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from emporos.research.swing.metrics import SwingMetrics, SwingStats
from emporos.research.swing.runner import ArmReport, CellReport

__all__ = ["format_report"]


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _benchmark_line(stats: SwingStats) -> str:
    return (
        f"benchmark (same-universe equal-weight buy-and-hold, same costs): net CAGR "
        f"{_pct(stats.net_cagr)}, net Sharpe {_num(stats.net_sharpe)}, max drawdown "
        f"{_pct(stats.max_drawdown)}, positive months {_pct(stats.positive_month_share, 0)}"
    )


def _row(arm: ArmReport) -> str:
    s = arm.outcome.stats
    return (
        f"{arm.label:<62} {'PASS' if arm.verdict.passed else 'reject':<6} "
        f"{arm.effective_start or 'n/a'!s:<10} {arm.first_entry_day or 'n/a'!s:<10} "
        f"{_pct(s.net_cagr):>7} {_pct(arm.outcome.adverse_stats.net_cagr):>7} "
        f"{_num(s.net_sharpe):>6} {_pct(s.positive_month_share, 0):>5} {_pct(s.worst_month):>7} "
        f"{_pct(s.max_drawdown):>6} {_num(s.monthly_t):>5} {s.round_trips:>5} "
        f"{_pct(s.positive_year_share, 0):>5} {_pct(s.max_instrument_share, 0):>5} "
        f"{_pct(arm.neighbour_share, 0):>5} {s.days_in_cash:>5}/{s.days}"
    )


HEADER = (
    f"{'arm':<62} {'result':<6} {'starts':<10} {'1st buy':<10} {'CAGR':>7} {'adv':>7} "
    f"{'Sharpe':>6} {'m+':>5} {'worst':>7} {'maxDD':>6} {'t':>5} {'trips':>5} {'y+':>5} "
    f"{'conc':>5} {'nbr':>5} {'cash days':>9}"
)


def format_report(report: CellReport) -> list[str]:
    arms: Sequence[ArmReport] = report.arms
    lines = [
        f"{report.slug}: Discovery {report.first_day}..{report.last_day}, {len(arms)} arms",
        _benchmark_line(arms[0].outcome.universe_stats),
        "",
        HEADER,
        *(_row(a) for a in arms),
    ]
    years = sorted({y for a in arms for y in SwingMetrics.yearly(a.outcome.arm)})
    lines += ["", "net return per calendar year (benchmark costs):"]
    lines.append(f"{'arm':<62} " + " ".join(f"{y:>7}" for y in years))
    for a in arms:
        by_year = SwingMetrics.yearly(a.outcome.arm)
        lines.append(f"{a.label:<62} " + " ".join(f"{_pct(by_year.get(y)):>7}" for y in years))
    lines += ["", "risk of ruin, 20-day block bootstrap, 12-month paths (§3.4):"]
    for a in arms:
        r = a.outcome.ruin
        tag = " (concentrated: the gate applies)" if a.aggressive else ""
        lines.append(
            f"{a.label:<62} P(drawdown>=30%) {_pct(r.p_drawdown_30)}  "
            f"P(12-month net<0) {_pct(r.p_year_negative)}  paths {r.paths} seed {r.seed}{tag}"
        )
    lines += ["", "why arms did not pass:"]
    for a in arms:
        lines.append(f"{a.label}: {'; '.join(a.verdict.failed_checks) or 'passed every check'}")
        lines.append(
            f"  {a.note}" + ("" if a.counted else "  [already in the ledger: not recounted]")
        )
    lines += ["", "REAL gaps a held position went through (traded through, never zeroed):"]
    for a in arms:
        held = a.real_gaps_held
        total = sum((e.pnl for e in held), Decimal(0))
        lines.append(f"{a.label}: {len(held)} gap(s), Rs {total:,.0f} in all")
        lines.extend(
            f"    {e.day} {e.instrument_id} gap {e.gap:+.1%}: Rs {e.pnl:+,.0f}" for e in held
        )
    lines += ["", *report.notes]
    return lines
