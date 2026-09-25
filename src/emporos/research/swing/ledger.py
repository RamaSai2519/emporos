"""Every Track A screen is a look, and every look is counted (PROFIT_PLAN §2.2, EM-223).

`docs/research/profit/screens.jsonl` is the Track A/B counterpart of the intraday screen ledger:
append-only, one line per arm, deduplicated by the arm's identity (running the same arm again is
the same look). Each line carries a `screen_id`, which is all `ProgramTrialCount` needs to count it
(`ScreenLedgerCounter` reads that field from any such file), plus the arm's key numbers, its
verdict and the path of its daily P&L file.

`DailyPnlStore` writes each arm's daily P&L series as a small CSV beside the ledger
(`docs/research/profit/pnl/<screen_id>.csv`: date, rupees), so arms can be combined by correlation
later without re-running them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from emporos.core.errors import ConfigurationError
from emporos.research.swing.screen import ArmOutcome, SwingVerdict

__all__ = [
    "DEFAULT_PROFIT_SCREENS", "DailyPnlStore", "JsonlSwingLedger", "SwingIdentity", "SwingRecord",
]  # fmt: skip

DEFAULT_PROFIT_SCREENS = Path("docs/research/profit/screens.jsonl")
DEFAULT_PNL_DIR = Path("docs/research/profit/pnl")


@dataclass(frozen=True)
class SwingIdentity:
    hypothesis: str  # the slug of its config/experiments/<slug>.yaml
    parameters: Mapping[str, str]
    universe: str
    first_day: date
    last_day: date
    capital: Decimal
    max_positions: int

    @property
    def screen_id(self) -> str:
        canonical = json.dumps(
            [
                self.hypothesis, sorted(self.parameters.items()), self.universe,
                self.first_day.isoformat(), self.last_day.isoformat(), str(self.capital),
                self.max_positions,
            ],
            separators=(",", ":"),
        )  # fmt: skip
        return f"SWG-{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class SwingRecord:
    identity: SwingIdentity
    outcome: ArmOutcome
    verdict: SwingVerdict
    neighbour_share: float | None
    recorded_at: datetime
    pnl_file: str
    real_gaps_held: int = 0
    real_gap_pnl: Decimal = Decimal(0)

    def as_document(self) -> dict[str, object]:
        i, o = self.identity, self.outcome
        s, a, u = o.stats, o.adverse_stats, o.universe_stats
        r = o.ruin
        return {
            "screen_id": i.screen_id,
            "track": "swing",
            "hypothesis": i.hypothesis,
            "parameters": dict(i.parameters),
            "universe": i.universe,
            "first_day": i.first_day.isoformat(),
            "last_day": i.last_day.isoformat(),
            "capital": str(i.capital),
            "max_positions": i.max_positions,
            "net_cagr": s.net_cagr,
            "adverse_net_cagr": a.net_cagr,
            "net_sharpe": s.net_sharpe,
            "universe_net_sharpe": u.net_sharpe,
            "universe_net_cagr": u.net_cagr,
            "positive_month_share": s.positive_month_share,
            "worst_month": s.worst_month,
            "monthly_t": s.monthly_t,
            "positive_year_share": s.positive_year_share,
            "max_drawdown": s.max_drawdown,
            "round_trips": s.round_trips,
            "days": s.days,
            "days_in_cash": s.days_in_cash,
            "months": s.months,
            "months_with_exposure": s.months_with_exposure,
            "positive_month_share_exposed": s.positive_month_share_exposed,
            "all_cash_months": s.all_cash_months,
            "max_instrument_share": s.max_instrument_share,
            "neighbour_share": self.neighbour_share,
            "p_drawdown_30": r.p_drawdown_30,
            "p_year_negative": r.p_year_negative,
            "bootstrap_seed": r.seed,
            "bootstrap_paths": r.paths,
            "bootstrap_block": r.block,
            "passed": self.verdict.passed,
            "failed_checks": list(self.verdict.failed_checks),
            "pnl_file": self.pnl_file,
            "real_gaps_held": self.real_gaps_held,
            "real_gap_pnl": str(self.real_gap_pnl),
            "recorded_at": self.recorded_at.isoformat(),
        }


class JsonlSwingLedger:
    """One JSON object per line, appended and never rewritten."""

    def __init__(self, path: Path = DEFAULT_PROFIT_SCREENS) -> None:
        self._path = path
        self._seen: set[str] | None = None

    def record(self, record: SwingRecord) -> bool:
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

    def count(self) -> int:
        return len(self._ids())

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


class DailyPnlStore:
    def __init__(self, directory: Path = DEFAULT_PNL_DIR) -> None:
        self._directory = directory

    def write(self, screen_id: str, outcome: ArmOutcome) -> str:
        """Write the arm's daily P&L; the path recorded in the ledger."""
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{screen_id}.csv"
        lines = ["date,pnl", *(f"{day.isoformat()},{pnl}" for day, pnl in outcome.daily_pnl)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path.as_posix()

    def read(self, path: Path) -> list[tuple[date, Decimal]]:
        rows = path.read_text(encoding="utf-8").splitlines()[1:]
        return [
            (date.fromisoformat(day), Decimal(pnl))
            for day, pnl in (row.split(",") for row in rows if row)
        ]
