"""Every Track B arm is a look, and every look is counted (PROFIT_PLAN §2.2, EM-230).

One JSON line per arm in `docs/research/profit/screens.jsonl`, the ledger Track A already writes;
`ProgramTrialCount` counts a line by its `screen_id` alone (`ScreenLedgerCounter`), so the two
tracks share it. Append-only, deduplicated by the arm's identity: running the same arm again is the
same look. The arm's daily P&L goes to a small CSV beside it, so arms can be combined later."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from emporos.core.errors import ConfigurationError
from emporos.research.option_screen.breakdown import CostBreakdown
from emporos.research.option_screen.run import ArmResult
from emporos.research.swing.ledger import DEFAULT_PNL_DIR, DEFAULT_PROFIT_SCREENS

__all__ = ["JsonlOptionLedger", "OptionIdentity", "OptionRecord", "write_daily_pnl"]


@dataclass(frozen=True)
class OptionIdentity:
    hypothesis: str  # the slug of its config/experiments/<slug>.yaml
    parameters: Mapping[str, str]
    underlying: str
    first_day: date
    last_day: date
    capital: Decimal

    @property
    def screen_id(self) -> str:
        canonical = json.dumps(
            [
                self.hypothesis, sorted(self.parameters.items()), self.underlying,
                self.first_day.isoformat(), self.last_day.isoformat(), str(self.capital),
            ],
            separators=(",", ":"),
        )  # fmt: skip
        return f"OPT-{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class OptionRecord:
    identity: OptionIdentity
    result: ArmResult
    recorded_at: datetime
    pnl_file: str

    def as_document(self) -> dict[str, object]:
        i, o = self.identity, self.result.outcome
        s, a, r = o.stats, o.adverse_stats, o.ruin
        return {
            "screen_id": i.screen_id,
            "track": "options",
            "hypothesis": i.hypothesis,
            "parameters": dict(i.parameters),
            "underlying": i.underlying,
            "first_day": i.first_day.isoformat(),
            "last_day": i.last_day.isoformat(),
            "capital": str(i.capital),
            "net_cagr": s.net_cagr,
            "adverse_net_cagr": a.net_cagr,
            "positive_month_share": s.positive_month_share,
            "months_with_exposure": s.months_with_exposure,
            "positive_month_share_exposed": s.positive_month_share_exposed,
            "negative_month_share": s.negative_month_share,
            "worst_month": s.worst_month,
            "monthly_t": s.monthly_t,
            "positive_year_share": s.positive_year_share,
            "max_drawdown": s.max_drawdown,
            "round_trips": s.round_trips,
            "days": s.days,
            "days_with_a_spread": s.days_with_a_spread,
            "net_pnl": s.net_pnl,
            "neighbour_share": self.result.neighbours,
            "skipped": dict(o.run.skipped),
            "breakdown": self._breakdown(),
            "p_drawdown_30": r.p_drawdown_30,
            "p_year_negative": r.p_year_negative,
            "bootstrap_seed": r.seed,
            "passed": self.result.verdict.passed,
            "failed_checks": list(self.result.verdict.failed_checks),
            "pnl_file": self.pnl_file,
            "recorded_at": self.recorded_at.isoformat(),
        }

    def _breakdown(self) -> dict[str, dict[str, object]]:
        """Per spread, in rupees, at zero slippage, benchmark and adverse costs."""
        o = self.result.outcome
        out: dict[str, dict[str, object]] = {}
        for name, run in (
            ("zero_slippage", o.frictionless_run),
            ("benchmark", o.run),
            ("adverse", o.adverse_run),
        ):
            if run is None:
                continue
            b = CostBreakdown.of(run)
            out[name] = {
                "trips": b.trips, "credit": b.credit, "gross": b.gross, "charges": b.charges,
                "net": b.net, "win_rate": b.win_rate, "average_win": b.average_win,
                "average_loss": b.average_loss, "worst_spread": b.worst_spread,
                "max_loss": b.max_loss, "exits": b.exits,
            }  # fmt: skip
        return out


def write_daily_pnl(directory: Path, screen_id: str, result: ArmResult) -> str:
    """The arm's daily P&L (date, rupees) at benchmark costs; the path recorded in the ledger."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{screen_id}.csv"
    lines = ["date,pnl", *(f"{d.day.isoformat()},{d.pnl}" for d in result.outcome.run.days)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.as_posix()


class JsonlOptionLedger:
    """One JSON object per line, appended and never rewritten."""

    def __init__(self, path: Path = DEFAULT_PROFIT_SCREENS) -> None:
        self._path = path
        self._seen: set[str] | None = None

    def record(self, record: OptionRecord) -> bool:
        """Append the arm. False, and nothing written, when this exact arm is already in."""
        seen = self._ids()
        screen_id = record.identity.screen_id
        if screen_id in seen:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_document(), sort_keys=True) + "\n")
        seen.add(screen_id)
        return True

    def _ids(self) -> set[str]:
        if self._seen is None:
            self._seen = self._read()
        return self._seen

    def _read(self) -> set[str]:
        if not self._path.exists():
            return set()
        ids: set[str] = set()
        for number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ids.add(str(json.loads(line)["screen_id"]))
            except (ValueError, KeyError, TypeError) as error:
                raise ConfigurationError(
                    f"{self._path}:{number}: not a screen record: {error}"
                ) from error
        return ids


__all__ += ["DEFAULT_PNL_DIR", "DEFAULT_PROFIT_SCREENS"]
