"""Previous-session breadth of the D1 names for the daily posture (PROFIT_PLAN §12.3, EM-239).

`SessionCloseTable` reduces each name's 5-minute bars ONCE to (session day, close), so a replay over
545 mornings never holds 148 bar series at once. `Breadth.lines(day)` then reads only sessions dated
before `day`: the previous session's close is known from 15:30 that day, so at 09:00 the next
morning nothing of `day` itself can be reached.

Lines: `breadth_names` (how many names had a previous-session close and a close before it),
`breadth_up_share` (share whose close rose over their prior close) and `breadth_above_20d_share`
(share whose close is above the mean of their last 20 session closes, itself included; a name with
fewer than 20 sessions is left out of that share only). Under `MIN_NAMES` names nothing is emitted:
a breadth of nine stocks is not breadth."""

from __future__ import annotations

import json
import os
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from emporos.core.clock import IST
from emporos.research.market_context.bars import BarLoader

__all__ = ["Breadth", "SessionCloseTable"]

MIN_NAMES = 30
AVERAGE_SESSIONS = 20


class SessionCloseTable:
    def __init__(self, closes: Mapping[str, Sequence[tuple[date, float]]]) -> None:
        self._days = {i: [d for d, _ in sorted(rows)] for i, rows in closes.items()}
        self._closes = {i: [c for _, c in sorted(rows)] for i, rows in closes.items()}

    @staticmethod
    def load(
        loader: BarLoader, instrument_ids: Sequence[str], first: date, last: date
    ) -> SessionCloseTable:
        table: dict[str, list[tuple[date, float]]] = {}
        for instrument_id in instrument_ids:
            last_close: dict[date, tuple[float, float]] = {}
            for bar in loader.load(instrument_id, first, last):
                stamp = bar.ts.timestamp()
                day = bar.ts.astimezone(IST).date()
                if day not in last_close or stamp > last_close[day][0]:
                    last_close[day] = (stamp, float(bar.close.amount))
            table[instrument_id] = [(d, v[1]) for d, v in sorted(last_close.items())]
        return SessionCloseTable(table)

    def save(self, path: Path, first: date, last: date) -> None:
        """Keep the table, with its window, so a rerun does not reread every name."""
        closes = {
            i: [[d.isoformat(), c] for d, c in zip(self._days[i], self._closes[i], strict=True)]
            for i in self._days
        }
        document = {"first": first.isoformat(), "last": last.isoformat(), "closes": closes}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def open(
        path: Path, first: date, last: date, instrument_ids: Sequence[str]
    ) -> SessionCloseTable | None:
        """The saved table if it was built for exactly this window and these names, else None."""
        if not path.exists():
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
        closes = document["closes"]
        if (
            document["first"] != first.isoformat()
            or document["last"] != last.isoformat()
            or sorted(closes) != sorted(instrument_ids)
        ):
            return None
        return SessionCloseTable(
            {i: [(date.fromisoformat(d), c) for d, c in rows] for i, rows in closes.items()}
        )

    @property
    def instrument_ids(self) -> list[str]:
        return sorted(self._days)

    def history_before(self, instrument_id: str, day: date, count: int) -> list[tuple[date, float]]:
        """The last `count` (session day, close) pairs strictly before `day`, oldest first."""
        days = self._days.get(instrument_id, [])
        stop = bisect_left(days, day)
        low = max(0, stop - count)
        return list(zip(days[low:stop], self._closes[instrument_id][low:stop], strict=True))


class Breadth:
    def __init__(self, table: SessionCloseTable) -> None:
        self._table = table

    def lines(self, day: date, previous_session: date) -> dict[str, float]:
        up = above = measured = averaged = 0
        for instrument_id in self._table.instrument_ids:
            history = self._table.history_before(instrument_id, day, AVERAGE_SESSIONS)
            if len(history) < 2 or history[-1][0] != previous_session:
                continue
            measured += 1
            up += history[-1][1] > history[-2][1]
            if len(history) == AVERAGE_SESSIONS:
                averaged += 1
                above += history[-1][1] > sum(c for _, c in history) / AVERAGE_SESSIONS
        if measured < MIN_NAMES:
            return {}
        lines = {"breadth_names": float(measured), "breadth_up_share": up / measured}
        if averaged >= MIN_NAMES:
            lines["breadth_above_20d_share"] = above / averaged
        return lines
