"""The plain-text report of a screened cell (EM-228, EM-229).

It carries what the head asked every Track A report to carry: the effective start, days in cash,
round trips, net per calendar year, the same-universe benchmark line, the §3.4 numbers for the
concentrated arms, and (A2) the reaction sessions used per year (the cell's own notes)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from emporos.research.swing.metrics import SwingMetrics, SwingStats
from emporos.research.swing.runner import ArmReport, CellReport
from emporos.research.swing.simulator import SwingRun

__all__ = ["format_report", "instrument_share_lines", "sub_period_lines"]


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _benchmark_line(stats: SwingStats, label: str) -> str:
    return (
        f"benchmark ({label}): net CAGR "
        f"{_pct(stats.net_cagr)}, net Sharpe {_num(stats.net_sharpe)}, max drawdown "
        f"{_pct(stats.max_drawdown)}, positive months {_pct(stats.positive_month_share, 0)}"
    )


def _row(arm: ArmReport) -> str:
    s = arm.outcome.stats
    return (
        f"{arm.label:<62} {'PASS' if arm.verdict.passed else 'reject':<6} "
        f"{arm.effective_start or 'n/a'!s:<10} {arm.first_entry_day or 'n/a'!s:<10} "
        f"{_pct(s.net_cagr):>7} {_pct(arm.outcome.adverse_stats.net_cagr):>7} "
        f"{_num(s.net_sharpe):>6} {_pct(s.positive_month_share_exposed, 0):>5} "
        f"{_pct(s.negative_month_share, 0):>5} {_pct(s.worst_month):>7} "
        f"{_pct(s.max_drawdown):>6} {_num(s.monthly_t):>5} {s.round_trips:>5} "
        f"{_pct(s.positive_year_share, 0):>5} {_pct(s.max_instrument_share, 0):>5} "
        f"{_pct(arm.neighbour_share, 0):>5} {s.days_in_cash:>5}/{s.days}"
    )


HEADER = (
    f"{'arm':<62} {'result':<6} {'starts':<10} {'1st buy':<10} {'CAGR':>7} {'adv':>7} "
    f"{'Sharpe':>6} {'m+exp':>5} {'m-all':>5} {'worst':>7} {'maxDD':>6} {'t':>5} {'trips':>5} "
    f"{'y+':>5} {'conc':>5} {'nbr':>5} {'cash days':>9}"
)


def format_report(report: CellReport) -> list[str]:
    arms: Sequence[ArmReport] = report.arms
    lines = [
        f"{report.slug}: Discovery {report.first_day}..{report.last_day}, {len(arms)} arms",
        _benchmark_line(arms[0].outcome.universe_stats, report.benchmark_label),
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
    lines += [
        "",
        "months (§3.2 as amended: >= 60% of months with exposure positive AND <= 40% of ALL "
        "months negative); the old all-months positive share for comparison:",
    ]
    for a in arms:
        s = a.outcome.stats
        lines.append(
            f"{a.label:<62} {s.months_with_exposure} months with exposure: "
            f"{_pct(s.positive_month_share_exposed, 0)} positive; all {s.months} months: "
            f"{_pct(s.negative_month_share, 0)} negative, {_pct(s.positive_month_share, 0)} "
            f"positive (old bar); {s.all_cash_months} all-cash months"
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


def _sub_row(label: str, s: SwingStats) -> str:
    return (
        f"{label:<40} {_pct(s.net_cagr):>7} {_num(s.net_sharpe):>6} "
        f"{_pct(s.positive_month_share_exposed, 0):>7} {_pct(s.negative_month_share, 0):>7} "
        f"{_pct(s.worst_month):>7} {_pct(s.max_drawdown):>6} {s.round_trips:>5}"
    )


def sub_period_lines(runs: Sequence[tuple[str, SwingRun]], first: date) -> list[str]:
    """Each named run's headline numbers from `first` on (a slice of the run, not a second look)."""
    lines = [
        "",
        f"sub-period from {first} (report only, benchmark costs; the runs' own, sliced):",
        f"{'':<40} {'CAGR':>7} {'Sharpe':>6} {'m+ exp':>7} {'m- all':>7} {'worst':>7} "
        f"{'maxDD':>6} {'trips':>5}",
    ]
    lines += [_sub_row(name, SwingMetrics.of(run.slice_from(first))) for name, run in runs]
    return lines


def instrument_share_lines(
    runs: Sequence[tuple[str, SwingRun]], names: dict[str, str], heading: str
) -> list[str]:
    """Each named instrument's net profit as a share of the run's total net profit (the
    concentration convention: trades' P&L, the total defined only when positive). For the index
    ETFs the §3.2 check does not apply, but the share is reported."""
    lines = ["", heading]
    for label, run in runs:
        by_name = run.pnl_by_instrument
        total = sum(by_name.values(), Decimal(0))
        if total <= 0:
            lines.append(f"{label:<40} no net trade profit (Rs {total:,.0f}): share undefined")
            continue
        parts = ", ".join(
            f"{symbol} {_pct(float(by_name.get(instrument, Decimal(0)) / total), 0)}"
            for instrument, symbol in names.items()
        )
        lines.append(f"{label:<40} of net trade profit Rs {total:,.0f}: {parts}")
    return lines
