"""The Crossing Ledger: moves seen IN REAL TIME, and what came after (EM-243, plan §0a).

The tradeable unit is not a move known at the close but a level crossed while the market is open.

* Intraday, stocks: the first 5-minute bar, per name-session and direction, at which the cumulative
  residual since the previous close (market, sector and group taken out; the overnight gap's
  residual is included: it is part of the move a trader sees) reaches `k x sigma15`, `sigma15`
  being the name's trailing 15-minute residual scale, k in {1.5, 2.0}. A move that is already there
  at the open is a crossing on the first bar and is flagged `at_open`.
* Intraday, sectors and NIFTY: the same with the sector's residual against NIFTY, and NIFTY's own
  return, each against its own 15-minute scale.
* Swing, stocks: a session that CLOSES at a residual of 2 sigma or more (daily scale); the entry is
  the next session's open.

Forward paths start at the entry reference: for an intraday crossing the close of the bar after
the crossing bar (the first bar boundary at least 2 minutes after the crossing), for a swing
crossing the next open. Every forward figure is in the crossing's direction (positive = the move
continued), as a percent, once as the residual and once as the raw return. `slip_pct` is what
the residual moved between the crossing bar's close and the entry reference: continuation already
gone before a trader could act."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from emporos.research.atlas.arrays import Floats
from emporos.research.atlas.decompose import Decomposition
from emporos.research.atlas.panel import SLOTS, slot_end_minute
from emporos.research.atlas.returns import Returns

__all__ = ["CROSSING_COLUMNS", "CrossingBlock", "CrossingRules", "CrossingScanner"]

PCT = 100.0
CLOSE_SLOT = 71  # the bar that ends at 15:15
LATER_15M, LATER_60M = 3, 12  # bars after the entry reference
SESSIONS = (1, 3, 5)
FORWARD = ("15m", "60m", "1515", "1d", "3d", "5d")

CROSSING_COLUMNS = (
    "kind", "name", "instrument_id", "sector", "group", "day", "direction", "k", "at_open",
    "crossed_minute", "entry_minute", "move_so_far_pct", "gap_pct", "level_pct", "sigma_pct",
    "entry_price", "slip_pct",
    *(f"resid_{h}_pct" for h in FORWARD), *(f"raw_{h}_pct" for h in FORWARD),
)  # fmt: skip


@dataclass(frozen=True)
class CrossingRules:
    ks: tuple[float, ...] = (1.5, 2.0)
    swing_sigma: float = 2.0
    first_day: date = date(2017, 11, 1)


@dataclass
class CrossingBlock:
    """Columns of crossings, appended row by row and read back as plain lists."""

    columns: dict[str, list[object]] = field(
        default_factory=lambda: {c: [] for c in CROSSING_COLUMNS}
    )

    def add(self, **row: object) -> None:
        for name in CROSSING_COLUMNS:
            self.columns[name].append(row[name])

    def extend(self, other: CrossingBlock) -> None:
        for name in CROSSING_COLUMNS:
            self.columns[name].extend(other.columns[name])

    def __len__(self) -> int:
        return len(self.columns["day"])


class CrossingScanner:
    def __init__(self, sessions: tuple[date, ...], rules: CrossingRules | None = None) -> None:
        self._sessions, self._rules = sessions, rules or CrossingRules()

    def intraday(
        self, who: tuple[str, str, str, str], own: Returns, deco: Decomposition, kind: str
    ) -> CrossingBlock:
        """`who` = (name, instrument id, sector, group)."""
        block = CrossingBlock()
        path, sigma15 = deco.path, deco.sigma15
        cumulative = np.nancumsum(np.nan_to_num(deco.resid))
        for k in self._rules.ks:
            level = k * sigma15
            for direction in (1, -1):
                hit = direction * path >= level[:, None]
                hit &= ~np.isnan(path) & ~np.isnan(level)[:, None]
                first = hit.argmax(axis=1)
                for d in np.flatnonzero(hit.any(axis=1)):
                    if self._sessions[d] >= self._rules.first_day and first[d] + 1 < SLOTS:
                        self._add_intraday(
                            block, who, own, deco, cumulative, kind, int(d), int(first[d]),
                            direction, k, float(level[d]),
                        )  # fmt: skip
        return block

    def _add_intraday(
        self, block: CrossingBlock, who: tuple[str, str, str, str], own: Returns,
        deco: Decomposition, cumulative: Floats, kind: str, d: int, i: int, direction: int,
        k: float, level: float,
    ) -> None:  # fmt: skip
        path, r = deco.path[d], i + 1  # r: the entry reference bar
        entry = own.close[d, r]
        if np.isnan(entry):
            return
        row: dict[str, object] = {
            "kind": kind, "name": who[0], "instrument_id": who[1], "sector": who[2],
            "group": who[3], "day": self._sessions[d], "direction": direction, "k": k,
            "at_open": i == 0, "crossed_minute": slot_end_minute(i),
            "entry_minute": slot_end_minute(r), "move_so_far_pct": direction * path[i] * PCT,
            "gap_pct": direction * deco.gap_resid[d] * PCT, "level_pct": level * PCT,
            "sigma_pct": deco.sigma15[d] * PCT, "entry_price": float(entry),
            "slip_pct": direction * (path[r] - path[i]) * PCT,
        }  # fmt: skip
        resid: dict[str, float] = {}
        raw: dict[str, float] = {}
        for label, slot in (("15m", r + LATER_15M), ("60m", r + LATER_60M), ("1515", CLOSE_SLOT)):
            ok = slot < SLOTS and slot > r
            resid[label] = direction * (path[slot] - path[r]) if ok else np.nan
            raw[label] = direction * (own.close[d, slot] / entry - 1) if ok else np.nan
        last = np.flatnonzero(~np.isnan(path))[-1]
        for h in SESSIONS:
            label = f"{h}d"
            if d + h >= len(cumulative) or np.isnan(own.level[d + h]):
                resid[label] = raw[label] = np.nan
                continue
            resid[label] = direction * (path[last] - path[r] + cumulative[d + h] - cumulative[d])
            raw[label] = direction * (own.level[d + h] / entry - 1)
        for label in FORWARD:
            row[f"resid_{label}_pct"] = resid[label] * PCT
            row[f"raw_{label}_pct"] = raw[label] * PCT
        block.add(**row)

    def swing(
        self, who: tuple[str, str, str, str], own: Returns, deco: Decomposition
    ) -> CrossingBlock:
        """A close at `swing_sigma` or more: entered at the next session's open."""
        block = CrossingBlock()
        cumulative = np.nancumsum(np.nan_to_num(deco.resid))
        with np.errstate(invalid="ignore"):
            big = np.flatnonzero(np.abs(deco.resid) >= self._rules.swing_sigma * deco.sigma)
        for d in big:
            if self._sessions[d] < self._rules.first_day or d + 1 >= len(cumulative):
                continue
            direction = 1 if deco.resid[d] > 0 else -1
            entry = own.open0[d + 1]
            if np.isnan(entry) or np.isnan(deco.path[d + 1]).all():
                continue
            row: dict[str, object] = {
                "kind": "swing_stock", "name": who[0], "instrument_id": who[1], "sector": who[2],
                "group": who[3], "day": self._sessions[d], "direction": direction, "k": 0.0,
                "at_open": False, "crossed_minute": 15 * 60 + 30, "entry_minute": 9 * 60 + 15,
                "move_so_far_pct": direction * deco.resid[d] * PCT,
                "gap_pct": direction * deco.gap_resid[d + 1] * PCT,
                "level_pct": self._rules.swing_sigma * deco.sigma[d] * PCT,
                "sigma_pct": deco.sigma[d] * PCT, "entry_price": float(entry), "slip_pct": 0.0,
            }  # fmt: skip
            intraday = deco.path[d + 1][np.flatnonzero(~np.isnan(deco.path[d + 1]))[-1]]
            first_day_resid = intraday - deco.gap_resid[d + 1]  # from the open, not the close
            for label in ("15m", "60m", "1515"):
                row[f"resid_{label}_pct"] = row[f"raw_{label}_pct"] = np.nan
            for h in SESSIONS:
                label = f"{h}d"
                if d + h >= len(cumulative) or np.isnan(own.level[d + h]):
                    row[f"resid_{label}_pct"] = row[f"raw_{label}_pct"] = np.nan
                    continue
                more = cumulative[d + h] - cumulative[d + 1]
                row[f"resid_{label}_pct"] = direction * (first_day_resid + more) * PCT
                row[f"raw_{label}_pct"] = direction * (own.level[d + h] / entry - 1) * PCT
            block.add(**row)
        return block
