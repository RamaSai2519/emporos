"""The day's largest confirmed overnight shock, faded to the close (EM-216, cell
L17-daily-top-shock-fade; docs/research/edge-search/review-2.md §3).

Per instrument this is the EM-203 first-hour shock reversal unchanged (`ShockReversalRules` at the
declared gap floor and a fixed 0.25 retrace floor): a signal at the close of the bar starting 10:10,
against the gap, held to the 15:15 square-off. What is new is that every such signal is also
recorded as a `ShockCandidate` scored by |gap| (known at 09:15), so that a cross-sectional
selection (`research.daily_selection`) can keep only the day's top one across the universe. The
candidates are the SIGNALS, recorded whether or not the order later fills: the selection must not
see fills.

`news = no_results` drops, per instrument, the session that reacts to its own results filing
(`event_days.reaction_days`), before the signal is recorded, so such a day never competes.

Not parity-proven against an engine strategy, so its screens are advisory.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from itertools import product
from typing import Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import (
    EntryIntent,
    IntradayScan,
    ScanExecution,
    ScanIntent,
    ScanRules,
)
from emporos.research.scans.event_days import InstrumentSymbols, reaction_days
from emporos.research.scans.regime_gate import DayGate, DayGatedRules
from emporos.research.scans.shock_reversal import ShockReversalParameters, ShockReversalRules
from emporos.research.screen_trades import ScreenTrade

__all__ = [
    "RETRACE_MIN",
    "AllDays",
    "CandidateBook",
    "EverySession",
    "ExcludedDays",
    "NewsFilter",
    "RecordingRules",
    "SessionFilter",
    "ShockCandidate",
    "TopShockParameters",
    "TopShockScan",
    "WithoutResultsReaction",
    "declared_arms",
]

RETRACE_MIN = Decimal("0.25")  # the parent's rule (EM-203/204), fixed by the declaration
_SESSION_OPEN = time(9, 15)


class NewsFilter(StrEnum):
    ALL = "all"
    NO_RESULTS = "no_results"


@dataclass(frozen=True)
class TopShockParameters:
    gap_floor_pct: Decimal
    news: NewsFilter

    def __post_init__(self) -> None:
        if self.gap_floor_pct <= 0:
            raise ValueError("the gap floor must be positive")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> TopShockParameters:
        return cls(Decimal(point["gap_floor_pct"]), NewsFilter(point["news"]))

    def as_point(self) -> dict[str, str]:
        return {"gap_floor_pct": str(self.gap_floor_pct), "news": self.news.value}

    @property
    def reversal(self) -> ShockReversalParameters:
        return ShockReversalParameters(self.gap_floor_pct, RETRACE_MIN)


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[TopShockParameters]:
    """Every arm of the declared grid. The declaration must name exactly this cell's parameters."""
    expected = ["gap_floor_pct", "news"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [TopShockParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


@dataclass(frozen=True)
class ShockCandidate:
    """A signal one instrument gave on one session, and how large its shock was."""

    instrument_id: str
    day: date
    score: Decimal  # |09:15 open / previous close - 1|


class CandidateBook:
    """Collects the candidates a scan signals, across every instrument it is given."""

    def __init__(self) -> None:
        self._candidates: list[ShockCandidate] = []

    def record(self, candidate: ShockCandidate) -> None:
        self._candidates.append(candidate)

    @property
    def candidates(self) -> tuple[ShockCandidate, ...]:
        return tuple(self._candidates)


class RecordingRules:
    """`ScanRules` that pass the inner rules' intents through unchanged and record every entry
    signal as a candidate scored by the session's |gap|."""

    def __init__(self, inner: ScanRules, instrument_id: str, book: CandidateBook) -> None:
        self._inner = inner
        self._instrument_id = instrument_id
        self._book = book
        self._day: date | None = None
        self._day_open: Decimal | None = None
        self._previous_close: Decimal | None = None
        self._last_close: Decimal | None = None

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        moment = bar.ts.astimezone(IST)
        if moment.date() != self._day:
            self._day = moment.date()
            self._previous_close = self._last_close
            self._day_open = bar.open.amount if moment.time() == _SESSION_OPEN else None
        self._last_close = bar.close.amount
        intent = self._inner.observe(bar, live=live, held=held, can_afford=can_afford)
        if isinstance(intent, EntryIntent) and self._scored():
            assert self._day is not None and self._day_open is not None
            assert self._previous_close is not None
            score = abs(self._day_open / self._previous_close - 1)
            self._book.record(ShockCandidate(self._instrument_id, self._day, score))
        return intent

    def _scored(self) -> bool:
        return self._day_open is not None and bool(self._previous_close)


class SessionFilter(Protocol):
    """Which of one instrument's sessions may compete (decided before any signal is recorded)."""

    def gate(self, instrument_id: str, bars: Sequence[Candle]) -> DayGate: ...


class AllDays:
    """A `DayGate` that refuses nothing."""

    def allows(self, day: date) -> bool:
        return True


@dataclass(frozen=True)
class ExcludedDays:
    """A `DayGate` that refuses a fixed set of sessions."""

    days: frozenset[date]

    def allows(self, day: date) -> bool:
        return day not in self.days


class EverySession:
    """`news = all`."""

    def gate(self, instrument_id: str, bars: Sequence[Candle]) -> DayGate:
        return AllDays()


class WithoutResultsReaction:
    """`news = no_results`: refuses the sessions that react to the instrument's own results
    filings, decided from the filings' public times and its own sessions (`reaction_days`)."""

    def __init__(
        self, events: Mapping[str, Sequence[datetime]], symbols: InstrumentSymbols
    ) -> None:
        self._events = events
        self._symbols = symbols

    def gate(self, instrument_id: str, bars: Sequence[Candle]) -> DayGate:
        sessions = sorted({bar.ts.astimezone(IST).date() for bar in bars})
        published = self._events.get(self._symbols.symbol(instrument_id), ())
        return ExcludedDays(reaction_days(published, sessions).days)


class TopShockScan:
    """A `SignalScan` that trades every confirmed shock of one arm and records it as a candidate.
    The cell run keeps only the trades the daily selection chooses from `candidates`."""

    name = "daily_top_shock_fade"

    def __init__(
        self, parameters: TopShockParameters, execution: ScanExecution, sessions: SessionFilter
    ) -> None:
        self._parameters = parameters
        self._execution = execution
        self._sessions = sessions
        self._book = CandidateBook()

    @property
    def candidates(self) -> tuple[ShockCandidate, ...]:
        return self._book.candidates

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        gate = self._sessions.gate(instrument_id, bars)
        reversal = self._parameters.reversal
        cutoff = self._execution.no_new_entries_after
        return IntradayScan(
            self.name,
            lambda: RecordingRules(
                DayGatedRules(ShockReversalRules(reversal, cutoff), gate), instrument_id, self._book
            ),
            self._execution,
        ).scan(instrument_id, bars)
