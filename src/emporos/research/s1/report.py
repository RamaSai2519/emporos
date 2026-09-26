"""The S1 report and its counted-looks ledger lines (EM-219 S1)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from emporos.research.s1.metrics import Bars
from emporos.research.s1.runner import ArmOutcome

__all__ = ["S1Ledger", "TRIAL_LOG", "arm_table"]

HYPOTHESIS = "s1-size-target-trail"
DEFAULT_LEDGER = Path("docs/research/profit/screens.jsonl")


def arm_table(outcomes: Sequence[ArmOutcome], bars: Bars) -> list[str]:
    lines = [
        "arm                                trades  net bench  net adverse   win  dailyT  maxDD"
        "   mo+  killed on   ctrl p  skipped  bars failed"
    ]
    for o in outcomes:
        m = o.metrics
        why = m.failures(bars, o.control_p)
        p = "n/a" if o.control_p is None else f"{o.control_p:.3f}"
        lines.append(
            f"{o.arm.name:<34} {m.trades:>6} {m.net_benchmark:>10,.0f} {m.net_adverse:>12,.0f} "
            f"{m.win_rate:>5.0%} {m.daily_t:>7.2f} {m.max_drawdown:>6,.0f} "
            f"{m.months_positive:>5.0%} {str(m.killed_on) if m.killed_on else '-':>10} {p:>8} "
            f"{sum(o.skipped.values()):>8}  {len(why)}"
        )
    return lines


TRIAL_LOG = (
    "Trial log: a first counting run with the loss limits on showed most arms hitting the Rs 25,000"
    " kill by Jan-Feb 2024. It was seen by Agent 2 before the declared run and was not sent to the"
    " head. No declared parameter, grid value or bar was changed after it."
)


class S1Ledger:
    """One line per arm and window in the program's counted-looks ledger."""

    def __init__(self, path: Path = DEFAULT_LEDGER) -> None:
        self._path = path

    def _known(self) -> set[str]:
        if not self._path.exists():
            return set()
        lines = self._path.read_text(encoding="utf-8").splitlines()
        return {json.loads(line).get("screen_id", "") for line in lines if line.strip()}

    def append(self, outcome: ArmOutcome, window: str, bars: Bars, recorded_at: datetime) -> bool:
        screen_id = f"{HYPOTHESIS}:{outcome.arm.name}:{window}"
        if screen_id in self._known():
            return False
        m = outcome.metrics
        document: dict[str, object] = {
            "screen_id": screen_id, "track": "s1", "hypothesis": HYPOTHESIS,
            "arm": outcome.arm.name, "book": outcome.arm.book, "window": window,
            "trades": m.trades, "net_benchmark": round(m.net_benchmark, 2),
            "net_adverse": round(m.net_adverse, 2), "daily_t": m.daily_t,
            "max_drawdown": round(m.max_drawdown, 2), "months_positive": m.months_positive,
            "killed_on": m.killed_on.isoformat() if m.killed_on else None,
            "control_p": outcome.control_p,
            "failed_checks": m.failures(bars, outcome.control_p),
            "passed": not m.failures(bars, outcome.control_p),
            "recorded_at": recorded_at.isoformat(),
        }  # fmt: skip
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(document, sort_keys=True) + "\n")
        return True
