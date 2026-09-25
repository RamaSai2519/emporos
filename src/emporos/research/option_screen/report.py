"""The plain-text table of a Track B cell (EM-230): every number the head asked to see for each arm,
so a reader can judge a result without opening the ledger."""

from __future__ import annotations

from collections.abc import Sequence

from emporos.options.backtest import BacktestResult
from emporos.research.option_screen.breakdown import CostBreakdown
from emporos.research.option_screen.run import ArmResult

__all__ = ["arm_lines", "breakdown_lines", "closure_lines", "table"]


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}"


def table(results: Sequence[ArmResult]) -> list[str]:
    lines = [
        f"{'arm':<28} {'verdict':<13} {'trips':>5} {'cagr%':>6} {'adv%':>6} {'mo+%':>5} "
        f"{'exp+%':>5} {'mo-%':>5} {'worst%':>7} {'t':>5} {'yr+%':>5} {'dd%':>5} {'nbr':>4} "
        f"{'pdd30':>6}"
    ]
    for r in results:
        s, a = r.outcome.stats, r.outcome.adverse_stats
        t = "n/a" if s.monthly_t is None else f"{s.monthly_t:.2f}"
        verdict = "PASS" if r.verdict.passed else "SCREEN_REJECT"
        lines.append(
            f"{r.arm.label:<28} {verdict:<13} {s.round_trips:>5} {_pct(s.net_cagr):>6} "
            f"{_pct(a.net_cagr):>6} {_pct(s.positive_month_share):>5} "
            f"{_pct(s.positive_month_share_exposed):>5} {_pct(s.negative_month_share):>5} "
            f"{_pct(s.worst_month):>7} "
            f"{t:>5} {_pct(s.positive_year_share):>5} {_pct(s.max_drawdown):>5} "
            f"{_pct(r.neighbours):>4} {r.outcome.ruin.p_drawdown_30:>6.3f}"
        )
    return lines


def arm_lines(result: ArmResult) -> list[str]:
    """The detail under the table for one arm: what was skipped and why, days with a spread on, the
    ruin numbers and the checks it failed."""
    o = result.outcome
    skipped = ", ".join(f"{k} {v}" for k, v in sorted(o.run.skipped.items())) or "none"
    ruin = o.ruin
    return [
        f"{result.arm.label}: {o.stats.months} months from {o.first_day}, "
        f"{o.stats.days_with_a_spread}/{o.stats.days} days with a spread on, "
        f"net Rs {o.stats.net_pnl:,.0f}",
        f"  skipped: {skipped}",
        f"  ruin (block bootstrap, seed {ruin.seed}): P(drawdown >= 30%) {ruin.p_drawdown_30:.3f}, "
        f"P(year < 0) {ruin.p_year_negative:.3f}, median year {ruin.median_year_return * 100:.1f}%",
        f"  failed: {'; '.join(result.verdict.failed_checks) or 'none'}",
    ]


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _row(name: str, b: CostBreakdown) -> str:
    share = "n/a" if b.charges_share_of_credit is None else f"{b.charges_share_of_credit:.0%}"
    return (
        f"  {name:<12} trips {b.trips:>4}  credit {_money(b.credit):>6}  "
        f"gross {_money(b.gross):>6}  "
        f"charges {_money(b.charges):>5} ({share} of credit)  net {_money(b.net):>6}  "
        f"win {b.win_rate:>4.0%}  avg win {_money(b.average_win):>5}  "
        f"avg loss {_money(b.average_loss):>6}  worst spread {_money(b.worst_spread):>7}  "
        f"max loss {_money(b.max_loss):>6}"
    )


def _runs(result: ArmResult) -> list[tuple[str, BacktestResult | None]]:
    o = result.outcome
    return [("zero slippage", o.frictionless_run), ("benchmark", o.run), ("adverse", o.adverse_run)]


def breakdown_lines(result: ArmResult) -> list[str]:
    """Per spread, in rupees: where the credit went, at zero slippage, benchmark and adverse."""
    lines = [f"{result.arm.label}: per spread, rupees"]
    for name, run in _runs(result):
        if run is not None:
            lines.append(_row(name, CostBreakdown.of(run)))
    return lines


def closure_lines(results: Sequence[ArmResult]) -> list[str]:
    """The declared closure test (B1b): does any arm's zero-slippage gross reach a multiple of its
    charges? One explicit line either way. Empty for a cell that declares no such test."""
    if not results or results[0].arm.design.closure_multiple is None:
        return []
    multiple = results[0].arm.design.closure_multiple
    lines, reached = [], False
    for r in results:
        run = r.outcome.frictionless_run
        b = CostBreakdown.of(run) if run is not None else None
        ratio = None if b is None else b.gross_to_charges
        reached = reached or (ratio is not None and ratio >= float(multiple))
        shown = "n/a" if ratio is None else f"{ratio:.2f}x"
        lines.append(f"  {r.arm.label}: zero-slippage gross = {shown} of its charges")
    verdict = (
        f"CLOSURE TEST NOT TRIGGERED: an arm has zero-slippage gross >= {multiple}x its charges"
        if reached
        else f"CLOSED: no arm's zero-slippage gross per spread reaches {multiple}x its charges, "
        "so index put-spread selling is closed at this capital"
    )
    return [f"closure test ({multiple}x charges):", *lines, verdict]
