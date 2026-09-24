"""The name leading the index on a strongly trending first hour, ridden to the close (EM-227, cell
L5-index-trend-day-leader; config/experiments/l5-index-trend-day-leader.yaml).

Per session the NIFTY 50 first-hour move (09:15 open to the close of the bar starting 10:10) is the
day gate and its sign is the day's direction. Per instrument, a session is a CANDIDATE when the gate
is open, the name's own first-hour move has the index's sign and is larger than the index's, and
the name has a first-hour volume baseline (at least 10 of its previous 20 sessions with a complete
first hour; today is never in its own baseline). The candidate is scored by the arm's rank key
(participation or extension) and recorded whether or not its order later fills, so a cross-sectional
selection (`research.daily_selection`) can keep the day's top one from signals alone.

Everything the rules read is known at the close of the 10:10 bar: no later bar of the index or of
the name reaches a decision. Not parity-proven against an engine strategy, so screens are advisory.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from itertools import product

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import (
    EntryIntent,
    IntradayScan,
    ScanExecution,
    ScanIntent,
    entries_open,
)
from emporos.research.screen_trades import ScreenTrade

__all__ = [
    "BASELINE_MINIMUM",
    "BASELINE_SESSIONS",
    "FIRST_HOUR_BARS",
    "IndexFirstHour",
    "IndexTrendLeaderScan",
    "LeaderCandidate",
    "LeaderParameters",
    "LeaderRules",
    "RankKey",
    "declared_arms",
]

FIRST_HOUR_BARS = 12  # 09:15 .. 10:10, each bar 5 minutes
BASELINE_SESSIONS = 20  # the declaration's window of previous sessions
BASELINE_MINIMUM = 10  # of which at least this many must have a complete first hour
_SESSION_OPEN = time(9, 15)
_DECISION = time(10, 10)  # the START of the bar whose close ends the first hour
_PERCENT = Decimal(100)


class RankKey(StrEnum):
    VOLUME = "volume"  # first-hour volume over the name's own baseline: participation
    MOVE = "move"  # the name's same-direction first-hour move: extension


@dataclass(frozen=True)
class LeaderParameters:
    index_move_min_pct: Decimal
    rank_key: RankKey

    def __post_init__(self) -> None:
        if self.index_move_min_pct <= 0:
            raise ValueError("the index move floor must be positive")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> LeaderParameters:
        return cls(Decimal(point["index_move_min_pct"]), RankKey(point["rank_key"]))

    def as_point(self) -> dict[str, str]:
        return {
            "index_move_min_pct": str(self.index_move_min_pct),
            "rank_key": self.rank_key.value,
        }


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[LeaderParameters]:
    """Every arm of the declared grid. The declaration must name exactly this cell's parameters."""
    expected = ["index_move_min_pct", "rank_key"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [LeaderParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


@dataclass(frozen=True)
class IndexFirstHour:
    """The index's SIGNED first-hour move (a fraction) on each session that has both its 09:15 open
    and the close of its 10:10 bar. A session missing either has no move, so no name trades it."""

    moves: Mapping[date, Decimal]

    @classmethod
    def from_bars(cls, bars: Sequence[Candle]) -> IndexFirstHour:
        opens: dict[date, Decimal] = {}
        closes: dict[date, Decimal] = {}
        for bar in bars:
            moment = bar.ts.astimezone(IST)
            if moment.time() == _SESSION_OPEN:
                opens[moment.date()] = bar.open.amount
            elif moment.time() == _DECISION:
                closes[moment.date()] = bar.close.amount
        return cls({d: closes[d] / opens[d] - 1 for d in opens.keys() & closes.keys() if opens[d]})

    def move(self, day: date) -> Decimal | None:
        return self.moves.get(day)


@dataclass(frozen=True)
class LeaderCandidate:
    """A signal one instrument gave on one session, scored by the arm's rank key."""

    instrument_id: str
    day: date
    score: Decimal


class _Tally:
    """One session's first-hour bars for one instrument."""

    def __init__(self) -> None:
        self._starts: set[time] = set()
        self.volume = 0
        self.open: Decimal | None = None

    def add(self, bar: Candle, start: time) -> None:
        if start in self._starts:
            return
        self._starts.add(start)
        self.volume += bar.volume
        if start == _SESSION_OPEN:
            self.open = bar.open.amount

    @property
    def complete(self) -> bool:
        return len(self._starts) == FIRST_HOUR_BARS and self.open is not None


class LeaderRules:
    """`ScanRules` for one instrument that enter with the index on a candidate session."""

    def __init__(
        self,
        parameters: LeaderParameters,
        index: IndexFirstHour,
        no_new_entries_after: time,
        candidates: list[LeaderCandidate],
        instrument_id: str,
    ) -> None:
        self._p = parameters
        self._index = index
        self._cutoff = no_new_entries_after
        self._candidates = candidates
        self._instrument_id = instrument_id
        self._day: date | None = None
        self._tally = _Tally()
        # first-hour volume of each previous session; None where the first hour was not whole
        self._baseline: deque[int | None] = deque(maxlen=BASELINE_SESSIONS)

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        moment = bar.ts.astimezone(IST)
        if moment.date() != self._day:
            self._roll(moment.date())
        start = moment.time()
        if _SESSION_OPEN <= start <= _DECISION:
            self._tally.add(bar, start)
        if held is not None or start != _DECISION or not live or not can_afford:
            return None
        if not entries_open(bar, self._cutoff):
            return None
        side, score = self._judge(moment.date(), bar)
        if side is None or score is None:
            return None
        self._candidates.append(LeaderCandidate(self._instrument_id, moment.date(), score))
        return EntryIntent(side)

    def _roll(self, day: date) -> None:
        """A new session: yesterday's first hour joins the baseline; today's is never in its own."""
        if self._day is not None:
            self._baseline.append(self._tally.volume if self._tally.complete else None)
        self._day = day
        self._tally = _Tally()

    def _judge(self, day: date, bar: Candle) -> tuple[OrderSide | None, Decimal | None]:
        index_move = self._index.move(day)
        opened = self._tally.open
        if index_move is None or opened is None or not self._tally.complete:
            return None, None
        if abs(index_move) * _PERCENT < self._p.index_move_min_pct:
            return None, None
        move = bar.close.amount / opened - 1
        # in the index's direction, and ahead of it: the name is leading, not lagging
        same_way = self._sign(move) == self._sign(index_move)
        if not same_way or abs(move) <= abs(index_move):
            return None, None
        # the baseline is a candidate condition for BOTH keys, so the arms differ only in ranking
        ratio = self._participation()
        if ratio is None:
            return None, None
        side = OrderSide.BUY if index_move > 0 else OrderSide.SELL
        return side, abs(move) if self._p.rank_key is RankKey.MOVE else ratio

    def _participation(self) -> Decimal | None:
        """Today's first-hour volume over the mean of the baseline sessions with a whole first hour;
        none until at least `BASELINE_MINIMUM` of them exist."""
        whole = [v for v in self._baseline if v is not None]
        if len(whole) < BASELINE_MINIMUM:
            return None
        mean = Decimal(sum(whole)) / len(whole)
        return Decimal(self._tally.volume) / mean if mean > 0 else None

    @staticmethod
    def _sign(value: Decimal) -> int:
        return (value > 0) - (value < 0)


class IndexTrendLeaderScan:
    """A `RankedScan` that trades every candidate of one arm and records it as a scored candidate.
    The cell run keeps only the trades the daily selection chooses from `candidates`."""

    name = "index_trend_day_leader"

    def __init__(
        self, parameters: LeaderParameters, index: IndexFirstHour, execution: ScanExecution
    ) -> None:
        self._parameters = parameters
        self._index = index
        self._execution = execution
        self._candidates: list[LeaderCandidate] = []

    @property
    def candidates(self) -> tuple[LeaderCandidate, ...]:
        return tuple(self._candidates)

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        cutoff = self._execution.no_new_entries_after
        return IntradayScan(
            self.name,
            lambda: LeaderRules(
                self._parameters, self._index, cutoff, self._candidates, instrument_id
            ),
            self._execution,
        ).scan(instrument_id, bars)
