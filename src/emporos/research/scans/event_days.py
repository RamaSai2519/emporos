"""Event reaction days as a per-instrument day filter for a scan (EM-191, EDGE_SEARCH_PLAN §5 L2).

A results filing carries the exchange's own dissemination time (D5). The session that REACTS to it
is decided from that time and the instrument's own sessions, never from a price:

* published before the 09:15 open of a session: that session reacts;
* published after the 15:30 close (or on a day with no session): the next session reacts;
* published DURING a session: no reaction session, because the market saw it mid-day and there is
  no overnight gap to trade. Those events are skipped, and counted.

`EventReactionScan` is a `SignalScan` that hands each instrument's bars to a fresh inner scan whose
entries are gated to that instrument's reaction days. `DayGatedRules` already drops entries on days
a `DayGate` refuses; this adds the missing piece, a gate that depends on WHICH instrument is
scanned.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.research.results_filings import ResultsFiling, first_public_results
from emporos.research.scans.base import IntradayScan
from emporos.research.screen_trades import ScreenTrade

__all__ = [
    "MAX_REACTION_LAG_DAYS",
    "EventReactionScan",
    "InstrumentSymbols",
    "ReactionDays",
    "ReactionOutcome",
    "SetOfDays",
    "reaction_days",
    "results_by_symbol",
]

_OPEN = time(9, 15)
_CLOSE = time(15, 30)
# A weekend plus a holiday or two: a reaction session further than this from the event means the
# instrument's bars have a hole where the reaction session should be, so the event is not used.
MAX_REACTION_LAG_DAYS = 5


@dataclass(frozen=True)
class ReactionDays:
    days: frozenset[date]
    used: int
    in_session: int  # published while the market was open: no overnight gap, skipped
    no_session: int  # the reaction session is missing from the bars (a hole, or past the data)


@dataclass(frozen=True)
class ReactionOutcome:
    by_year: Mapping[int, int]  # reaction sessions used, per calendar year
    in_session: int
    no_session: int


def reaction_days(published: Iterable[datetime], sessions: Sequence[date]) -> ReactionDays:
    """The sessions that react to events published at `published` (IST-aware), given the
    instrument's own `sessions` (any order)."""
    ordered = sorted(set(sessions))
    have = set(ordered)
    days: set[date] = set()
    in_session = no_session = 0
    for moment in published:
        stamp = moment.astimezone(IST)
        day, at = stamp.date(), stamp.time()
        if not ordered or not ordered[0] <= day <= ordered[-1]:
            continue  # outside the bars this scan was given: not this split's event
        if day in have and _OPEN <= at < _CLOSE:
            in_session += 1
            continue
        target = day if day in have and at < _OPEN else _first_after(ordered, day)
        if target is None or target - day > timedelta(days=MAX_REACTION_LAG_DAYS):
            no_session += 1
            continue
        days.add(target)
    return ReactionDays(frozenset(days), len(days), in_session, no_session)


def _first_after(ordered: Sequence[date], day: date) -> date | None:
    return next((d for d in ordered if d > day), None)


@dataclass(frozen=True)
class SetOfDays:
    """A `DayGate` over a fixed set of sessions."""

    days: frozenset[date]

    def allows(self, day: date) -> bool:
        return day in self.days


class InstrumentSymbols:
    """`NSE:<token>` candle ids to the trading symbol events are keyed by (config/universe/d1)."""

    def __init__(self, symbol_by_instrument: Mapping[str, str]) -> None:
        self._map = dict(symbol_by_instrument)

    @classmethod
    def load(cls, path: Path) -> InstrumentSymbols:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or set(rows[0]) != {"Symbol", "Token"}:
            raise ValueError(f"{path}: expected a Symbol,Token table")
        return cls({f"NSE:{r['Token']}": r["Symbol"] for r in rows})

    def symbol(self, instrument_id: str) -> str:
        try:
            return self._map[instrument_id]
        except KeyError as error:
            raise ValueError(f"{instrument_id} is not in the symbol table") from error


def results_by_symbol(filings: Iterable[ResultsFiling]) -> dict[str, list[datetime]]:
    """One publication time per results season per name (the earliest filing of the season)."""
    out: dict[str, list[datetime]] = {}
    for filing in first_public_results(filings):
        out.setdefault(filing.symbol, []).append(filing.public_at)
    return out


class EventReactionScan:
    """Runs an inner scan on one instrument's bars with entries gated to its reaction days.

    `inner` builds the inner scan for a given gate. Every instrument scanned adds to `outcome`, so a
    cell can report how many reaction sessions it actually had per year (a missing quarter is a
    missed trade, and the reader must be told how many were missed)."""

    def __init__(
        self,
        name: str,
        inner: Callable[[SetOfDays], IntradayScan],
        events: Mapping[str, Sequence[datetime]],
        symbols: InstrumentSymbols,
    ) -> None:
        self.name = name
        self._inner = inner
        self._events = events
        self._symbols = symbols
        self._by_year: Counter[int] = Counter()
        self._in_session = 0
        self._no_session = 0

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        symbol = self._symbols.symbol(instrument_id)
        sessions = sorted({bar.ts.astimezone(IST).date() for bar in bars})
        reacted = reaction_days(self._events.get(symbol, ()), sessions)
        self._by_year.update(d.year for d in reacted.days)
        self._in_session += reacted.in_session
        self._no_session += reacted.no_session
        return self._inner(SetOfDays(reacted.days)).scan(instrument_id, bars)

    @property
    def outcome(self) -> ReactionOutcome:
        return ReactionOutcome(
            dict(sorted(self._by_year.items())), self._in_session, self._no_session
        )
