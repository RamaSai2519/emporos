"""The variants side by side (EM-240): one JSON summary per run, and a table read from them.

The summary holds only numbers the report already shows, so the table can be rebuilt without
re-running anything. `best_variant` applies the declaration's rule for the arbiter A/B: the highest
Dev net expectancy per trade at ADVERSE costs after token cost, among variants with at least 30
trades."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from emporos.eventtrader.replay.report import VariantReport

__all__ = ["MIN_TRADES_FOR_BEST", "best_variant", "load_summaries", "render_table", "summary_of"]

MIN_TRADES_FOR_BEST = 30


def _num(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def summary_of(report: VariantReport) -> dict[str, Any]:
    b, a, r = report.benchmark, report.adverse, report.result
    by_name: Counter[str] = Counter(t.symbol for t in r.trades)
    return {
        "variant": report.identity.variant,
        "snapshot": report.identity.snapshot,
        "prompt_hash": report.identity.prompt_hash,
        "events": r.stats.events,
        "verdicts": dict(r.stats.verdicts),
        "refused": dict(r.stats.refused),
        "no_entry": dict(r.stats.no_entry),
        "skipped": dict(r.stats.skipped),
        "killed_at": r.stats.killed_at.isoformat() if r.stats.killed_at else None,
        "trades": b.trades,
        "trades_by_name": dict(by_name.most_common()),
        "net_benchmark": _num(b.net_total),
        "net_adverse": _num(a.net_total),
        "token_cost": _num(b.token_cost),
        "expectancy_benchmark": _num(b.expectancy),
        "expectancy_adverse": _num(a.expectancy),
        "daily_t": b.daily_t,
        "max_drawdown": _num(b.max_drawdown),
        "p_loss": report.p_loss,
        "months_positive": [b.months_positive, b.months_with_trades],
        "top_name": b.top_name,
        "top_name_share": b.top_name_share,
        "control_p": report.control.p_value if report.control else None,
        "coin_mean": _num(report.control.coin_mean) if report.control else None,
        "baseline_net": _num(report.baseline.net_total) if report.baseline else None,
        "baseline_trades": report.baseline.trades if report.baseline else None,
        "bars": [
            {"name": bar.name, "passed": bar.passed, "value": bar.value, "need": bar.threshold}
            for bar in report.bars
        ],
        "fresh_calls": report.fresh_calls,
        "journal_hits": report.journal_hits,
        "prefiltered": dict(report.prefiltered),
    }


def load_summaries(directory: Path) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*-summary.json"))
    ]


def best_variant(summaries: Sequence[Mapping[str, Any]]) -> str | None:
    eligible = [
        s for s in summaries
        if s["trades"] >= MIN_TRADES_FOR_BEST and s["expectancy_adverse"] is not None
    ]  # fmt: skip
    if not eligible:
        return None
    return str(max(eligible, key=lambda s: s["expectancy_adverse"])["variant"])


def _f(value: float | None, spec: str = ",.0f") -> str:
    return "n/a" if value is None else format(value, spec)


def render_table(summaries: Sequence[Mapping[str, Any]]) -> str:
    if not summaries:
        return "no variant summaries\n"
    rows: list[tuple[str, Callable[[Mapping[str, Any]], str]]] = [
        ("variant", lambda s: s["variant"]),
        ("trades", lambda s: str(s["trades"])),
        ("net adverse", lambda s: _f(s["net_adverse"])),
        ("net benchmark", lambda s: _f(s["net_benchmark"])),
        ("expectancy benchmark", lambda s: _f(s["expectancy_benchmark"])),
        ("expectancy adverse", lambda s: _f(s["expectancy_adverse"])),
        ("token cost", lambda s: _f(s["token_cost"])),
        ("daily t", lambda s: _f(s["daily_t"], ".2f")),
        ("max drawdown", lambda s: _f(s["max_drawdown"])),
        ("P(lose Rs 25k / 12m)", lambda s: _f(s["p_loss"], ".3f")),
        ("months positive", lambda s: f"{s['months_positive'][0]}/{s['months_positive'][1]}"),
        ("top name share", lambda s: _f(s["top_name_share"], ".2f")),
        ("coin-flip p", lambda s: _f(s["control_p"], ".4f")),
        ("triage-only net (diag)", lambda s: _f(s["baseline_net"])),
        ("refusals", lambda s: str(sum(s["refused"].values()))),
        ("fresh / journal calls", lambda s: f"{s['fresh_calls']}/{s['journal_hits']}"),
        ("bars passed", lambda s: f"{sum(b['passed'] for b in s['bars'])}/{len(s['bars'])}"),
    ]
    width = max(len(name) for name, _ in rows)
    lines = [
        f"{name:<{width}}  " + "  ".join(f"{fn(s):>16}" for s in summaries) for name, fn in rows
    ]
    lines += ["", "bars, by variant:"]
    for s in summaries:
        lines.append(f"  {s['variant']}: " + "; ".join(
            f"{'PASS' if b['passed'] else 'FAIL'} {b['name']} {b['value']}" for b in s["bars"]
        ))  # fmt: skip
    lines += ["", "refusals by reason:"]
    for s in summaries:
        lines.append(f"  {s['variant']}: {dict(sorted(s['refused'].items()))}")
    lines += ["", "trades per instrument (top 10):"]
    for s in summaries:
        lines.append(f"  {s['variant']}: {dict(list(s['trades_by_name'].items())[:10])}")
    best = best_variant(summaries)
    lines += ["", f"best by the declared rule (>= {MIN_TRADES_FOR_BEST} trades, adverse "
              f"expectancy after tokens): {best or 'none qualifies'}"]  # fmt: skip
    return "\n".join(lines) + "\n"
