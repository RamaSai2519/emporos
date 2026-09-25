"""The daily posture's numbers, as known at 09:00 IST (PROFIT_PLAN §12.3, EM-239).

`NumericPostureInputs.for_day(day)` is the `PostureInputs` the posture call reads: no headlines (a
timestamped headline source was parked), a `state` of stable snake_case keys built by independent
sections, and `decision_at` = 09:00 IST on `day`. Each section answers `lines(day, previous_session,
at)` and may read only what was known by `at`:

* `IndexState`: NIFTY's previous close, its previous-session move and 5 / 20 session returns; INDIA
  VIX's previous close, its previous-session change and its percentile among the last (up to 252)
  session closes, with the count so a short history (the span starts 2024-01-01) is visible.
* `BreadthSection`: the D1 names' previous-session breadth.
* `GlobalCuesSection`: the previous US/EU session's closes (S&P 500, Nasdaq, USD/INR, Brent, US
  10-year), from observations dated before `day`.
* `FilingCounts`: material filings (the attachment priority's MATERIAL categories) published from
  the previous session's close to `at`, as `material_filings_prev_day` and one line per category.

A number that cannot be known is left out, never filled in. The previous session is NIFTY's
last session before `day` (a Monday sees Friday), so a weekend's filings count on Monday."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Protocol

from emporos.core.clock import IST
from emporos.eventtrader.events import EventStore
from emporos.eventtrader.stages.stages import PostureInput
from emporos.research.filings.priority import MATERIAL
from emporos.research.market_context.bars import BarSeriesCache, IntradayBars
from emporos.research.market_context.breadth import Breadth
from emporos.research.market_context.global_cues import GlobalCues

__all__ = [
    "BreadthSection", "FilingCounts", "GlobalCuesSection", "IndexState", "NumericPostureInputs",
    "PostureSection",
]  # fmt: skip

POSTURE_TIME = time(9, 0)
SESSION_CLOSE = time(15, 30)
VIX_WINDOW = 252
MIN_VIX_SESSIONS = 60
_NIFTY_MOVES = (("nifty_prev_day_move_pct", 1), ("nifty_ret_5d_pct", 5), ("nifty_ret_20d_pct", 20))
_SLUG = re.compile(r"[^a-z0-9]+")


class PostureSection(Protocol):
    def lines(self, day: date, previous_session: date, at: datetime) -> dict[str, float]: ...


def _percent(latest: float, earlier: float) -> float | None:
    return (latest / earlier - 1) * 100 if earlier else None


class IndexState:
    def __init__(self, series: BarSeriesCache, nifty_id: str, vix_id: str) -> None:
        self._series, self._nifty, self._vix = series, nifty_id, vix_id

    def lines(self, day: date, previous_session: date, at: datetime) -> dict[str, float]:
        out: dict[str, float] = {}
        nifty = self._closes(self._nifty, day, at, 21, previous_session)
        if nifty:
            out["nifty_prev_close"] = nifty[-1]
            for name, back in _NIFTY_MOVES:
                if (
                    len(nifty) > back
                    and (move := _percent(nifty[-1], nifty[-1 - back])) is not None
                ):
                    out[name] = move
        vix = self._closes(self._vix, day, at, VIX_WINDOW, previous_session)
        if vix:
            out["india_vix"] = vix[-1]
            if len(vix) > 1 and (change := _percent(vix[-1], vix[-2])) is not None:
                out["india_vix_change_pct"] = change
            if len(vix) >= MIN_VIX_SESSIONS:
                out["india_vix_percentile"] = 100 * sum(v <= vix[-1] for v in vix) / len(vix)
                out["india_vix_percentile_sessions"] = float(len(vix))
        return out

    def _closes(
        self, instrument_id: str, day: date, at: datetime, count: int, previous_session: date
    ) -> list[float]:
        bars: IntradayBars = self._series.bars(instrument_id)
        pairs = bars.closes_before(day, count, at)
        return [c for _, c in pairs] if pairs and pairs[-1][0] == previous_session else []


class BreadthSection:
    def __init__(self, breadth: Breadth) -> None:
        self._breadth = breadth

    def lines(self, day: date, previous_session: date, at: datetime) -> dict[str, float]:
        return self._breadth.lines(day, previous_session)


class GlobalCuesSection:
    def __init__(self, cues: GlobalCues) -> None:
        self._cues = cues

    def lines(self, day: date, previous_session: date, at: datetime) -> dict[str, float]:
        return self._cues.lines(day)


class FilingCounts:
    def __init__(self, store: EventStore, categories: frozenset[str] = MATERIAL) -> None:
        self._store, self._categories = store, categories

    def lines(self, day: date, previous_session: date, at: datetime) -> dict[str, float]:
        since = datetime.combine(previous_session, SESSION_CLOSE, tzinfo=IST)
        counts = Counter(
            e.category
            for e in self._store.events_between(since, at)
            if e.usable_from <= at and e.category in self._categories
        )
        lines = {"material_filings_prev_day": float(sum(counts.values()))}
        for category, n in sorted(counts.items()):
            lines[f"material_filings_{_SLUG.sub('_', category.lower()).strip('_')[:40]}"] = float(n)
        return lines


class NumericPostureInputs:
    """A `PostureInputs` (eventtrader.posture): numbers only, `headlines` empty."""

    def __init__(
        self, sections: Sequence[PostureSection], nifty: BarSeriesCache, nifty_id: str
    ) -> None:
        self._sections, self._series, self._nifty = tuple(sections), nifty, nifty_id

    def for_day(self, day: date) -> PostureInput:
        at = datetime.combine(day, POSTURE_TIME, tzinfo=IST)
        earlier = self._series.bars(self._nifty).closes_before(day, 1, at)
        state: dict[str, str | float] = {}
        if earlier:
            previous = earlier[-1][0]
            for section in self._sections:
                state.update(section.lines(day, previous, at))
        return PostureInput([], state, at)
