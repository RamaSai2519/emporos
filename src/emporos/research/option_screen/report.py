"""The plain-text table of a Track B cell (EM-230): every number the head asked to see for each arm,
so a reader can judge a result without opening the ledger."""

from __future__ import annotations

from collections.abc import Sequence

from emporos.research.option_screen.run import ArmResult

__all__ = ["arm_lines", "table"]


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}"


def table(results: Sequence[ArmResult]) -> list[str]:
    lines = [
        f"{'arm':<28} {'verdict':<13} {'trips':>5} {'cagr%':>6} {'adv%':>6} {'mo+%':>5} "
        f"{'worst%':>7} {'t':>5} {'yr+%':>5} {'dd%':>5} {'nbr':>4} {'pdd30':>6}"
    ]
    for r in results:
        s, a = r.outcome.stats, r.outcome.adverse_stats
        t = "n/a" if s.monthly_t is None else f"{s.monthly_t:.2f}"
        verdict = "PASS" if r.verdict.passed else "SCREEN_REJECT"
        lines.append(
            f"{r.arm.label:<28} {verdict:<13} {s.round_trips:>5} {_pct(s.net_cagr):>6} "
            f"{_pct(a.net_cagr):>6} {_pct(s.positive_month_share):>5} {_pct(s.worst_month):>7} "
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
