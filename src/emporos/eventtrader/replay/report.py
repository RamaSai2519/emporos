"""What a Dev run leaves behind (EM-240, PROFIT_PLAN §12.5): the trades CSV, the daily P&L CSV, a
plain-text report with every bar evaluated, and one counted line in the screens ledger.

The ledger is append-only and every variant is a look (`screen_id` is what the trial counter reads).
Running the same variant on the same prompts and dates again is the same look, so it is not added
twice."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from emporos.eventtrader.replay.control import ControlResult
from emporos.eventtrader.replay.engine import RunResult
from emporos.eventtrader.replay.metrics import Bar, ScenarioMetrics
from emporos.eventtrader.replay.records import TradeRecord

__all__ = [
    "LlmLedger",
    "VariantIdentity",
    "VariantReport",
    "render_report",
    "write_daily_csv",
    "write_trades_csv",
]

DEFAULT_LEDGER = Path("docs/research/profit/screens.jsonl")
TRADE_COLUMNS = (
    "event_id", "symbol", "instrument", "product", "side", "quantity", "entry_ts", "entry_price",
    "exit_ts", "exit_price", "exit_reason", "stop_price", "risk", "gross_pnl", "cost_benchmark",
    "cost_adverse", "net_benchmark", "net_adverse", "token_cost_inr",
)  # fmt: skip


def _row(t: TradeRecord) -> list[object]:
    return [
        t.event_id, t.symbol, t.instrument.value, t.product.value, t.side.value, t.quantity,
        t.entry_ts.isoformat(), t.entry_price, t.exit_ts.isoformat(), t.exit_price,
        t.exit_reason.value, "" if t.stop_price is None else t.stop_price, t.risk, t.gross_pnl,
        t.cost_benchmark, t.cost_adverse, t.net_benchmark, t.net_adverse, t.token_cost_inr,
    ]  # fmt: skip


def write_trades_csv(path: Path, trades: tuple[TradeRecord, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(TRADE_COLUMNS)
        writer.writerows(_row(t) for t in trades)


def write_daily_csv(path: Path, benchmark: ScenarioMetrics, adverse: ScenarioMetrics) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    adverse_by_day = dict(adverse.daily)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["day", "net_benchmark", "net_adverse"])
        for day, value in benchmark.daily:
            writer.writerow([day.isoformat(), value, adverse_by_day.get(day, "")])


@dataclass(frozen=True)
class VariantIdentity:
    hypothesis: str  # the slug of its config/experiments/<slug>.yaml
    variant: str
    first_day: date
    last_day: date
    prompt_hash: str
    models: tuple[str, ...]
    snapshot: str = ""  # the frozen events snapshot every variant reads

    @property
    def screen_id(self) -> str:
        canonical = json.dumps(
            [self.hypothesis, self.variant, self.first_day.isoformat(), self.last_day.isoformat(),
             self.prompt_hash, sorted(self.models), self.snapshot],
            separators=(",", ":"),
        )  # fmt: skip
        return f"LLM-{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class VariantReport:
    identity: VariantIdentity
    result: RunResult
    benchmark: ScenarioMetrics
    adverse: ScenarioMetrics
    p_loss: float | None
    bars: tuple[Bar, ...]
    control: ControlResult | None = None
    baseline: ScenarioMetrics | None = None  # a diagnostic, never a gate
    baseline_rule: str = ""
    journal_hits: int = 0  # calls answered from the journal
    fresh_calls: int = 0  # calls that reached the model

    @property
    def passed(self) -> bool:
        return all(b.passed for b in self.bars)


class LlmLedger:
    def __init__(self, path: Path = DEFAULT_LEDGER) -> None:
        self._path = path

    def _known(self) -> set[str]:
        if not self._path.exists():
            return set()
        lines = self._path.read_text(encoding="utf-8").splitlines()
        return {json.loads(line).get("screen_id", "") for line in lines if line.strip()}

    def append(self, report: VariantReport, recorded_at: datetime, files: dict[str, str]) -> bool:
        """True when a line was added, False when the same variant was already recorded."""
        i, b, a = report.identity, report.benchmark, report.adverse
        if i.screen_id in self._known():
            return False
        document: dict[str, object] = {
            "screen_id": i.screen_id, "track": "llm", "hypothesis": i.hypothesis,
            "variant": i.variant, "first_day": i.first_day.isoformat(),
            "last_day": i.last_day.isoformat(), "prompt_hash": i.prompt_hash,
            "models": list(i.models), "snapshot": i.snapshot, "trades": b.trades,
            "net_benchmark": str(b.net_total),
            "net_adverse": str(a.net_total), "token_cost": str(b.token_cost),
            "max_drawdown": str(b.max_drawdown), "daily_t": b.daily_t, "p_loss": report.p_loss,
            "control_p": report.control.p_value if report.control else None,
            "failed_checks": [bar.name for bar in report.bars if not bar.passed],
            "passed": report.passed, "journal_hits": report.journal_hits,
            "fresh_calls": report.fresh_calls, "files": files,
            "recorded_at": recorded_at.isoformat(),
        }  # fmt: skip
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(document, sort_keys=True) + "\n")
        return True


def _money(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _metrics_lines(label: str, m: ScenarioMetrics) -> list[str]:
    win = "n/a" if m.win_rate is None else f"{m.win_rate:.0%}"
    t = "n/a" if m.daily_t is None else f"{m.daily_t:.2f}"
    return [
        f"  {label}: net {_money(m.net_total)} (trading {_money(m.net_trading)}, tokens "
        f"{_money(m.token_cost)}), trades {m.trades}, expectancy {_money(m.expectancy)}, win {win},"
        f" daily t {t}, max drawdown {_money(m.max_drawdown)}"
    ]


def render_report(report: VariantReport) -> str:
    i, r = report.identity, report.result
    lines = [
        f"{i.hypothesis} / {i.variant}: {i.first_day} .. {i.last_day} (Dev, descriptive)",
        f"  prompts {i.prompt_hash}; models {', '.join(i.models)}",
        f"  events snapshot {i.snapshot or 'n/a'}",
        "",
        f"events {r.stats.events}; verdicts {dict(sorted(r.stats.verdicts.items()))}",
        f"skipped {dict(sorted(r.stats.skipped.items()))}",
        f"calls: {report.fresh_calls} fresh, {report.journal_hits} answered from the journal",
        f"refused by reason {dict(sorted(r.stats.refused.items()))}",
        f"approved, not filled {dict(sorted(r.stats.no_entry.items()))}",
    ]
    if r.stats.killed_at is not None:
        lines.append(f"TOTAL-LOSS KILL at {r.stats.killed_at.isoformat()}")
    lines += ["", *_metrics_lines("benchmark", report.benchmark)]
    lines += _metrics_lines("adverse", report.adverse)
    lines.append(f"  months with trades positive {_share(report.benchmark)}")
    lines.append(
        f"  top name {report.benchmark.top_name} share {_pct(report.benchmark.top_name_share)}"
    )
    lines += ["", "bars (Dev: descriptive; the verdict is the one Test run of the frozen variant):"]
    lines += [
        f"  [{'PASS' if b.passed else 'FAIL'}] {b.name}: {b.value} (need {b.threshold})"
        for b in report.bars
    ]
    lines += ["", *_per_instrument(r.trades)]
    if report.control is not None:
        c = report.control
        lines += [
            "",
            f"coin-flip control ({c.scenario.value}): real net {_money(c.real_net)}, "
            f"coin mean {_money(c.coin_mean)}, {c.at_least_as_good} of {c.runs} runs at least as "
            f"good, p = {c.p_value:.4f}",
        ]
    if report.baseline is not None:
        lines += ["", f"triage-only baseline (DIAGNOSTIC, never a gate; {report.baseline_rule}):"]
        lines += _metrics_lines("benchmark", report.baseline)
    return "\n".join(lines) + "\n"


def _per_instrument(trades: tuple[TradeRecord, ...]) -> list[str]:
    """Trades and benchmark net by name, most trades first: shows concentration at a glance."""
    by_name: dict[str, list[Decimal]] = defaultdict(list)
    for t in trades:
        by_name[t.symbol].append(t.net_benchmark)
    ordered = sorted(by_name.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = [f"trades per instrument ({len(ordered)} names; net at benchmark, before tokens):"]
    lines += [
        f"  {name}: {len(nets)} trades, {_money(sum(nets, Decimal(0)))}" for name, nets in ordered
    ]
    return lines


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _share(m: ScenarioMetrics) -> str:
    return f"{m.months_positive} of {m.months_with_trades}"
