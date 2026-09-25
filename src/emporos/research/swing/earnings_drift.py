"""A2: post-earnings drift (config/experiments/a2-post-earnings-drift.yaml, EM-229).

Every rule below is a line of the declaration; nothing is added and nothing is tuned.

* An event is a results filing's first public time (the D5 ledger). `ReactionSessions` maps each to
  the session that reacts to it, from the exchange's timestamp and the name's own sessions only
  (before 09:15: that session; after the close or on a non-session day: the next; in-session:
  skipped and counted), exactly as `scans.event_days.reaction_days`.
* At the reaction session's close a name qualifies if its adjusted reaction return (`close /
  previous close - 1`) is at least `R` AND its volume that session is at least twice the mean of
  the 20 sessions before it (so it needs 21 sessions of history). Long only.
* Entry at the next open if a slot is free (`N` names at most) and the regime is on at the decision
  close. When more names qualify on one close than slots are free, the largest reaction returns are
  taken; the rest are skipped, not queued.
* Exit when the holding has been held `H` sessions (the H-th session after the fill session: the
  decision at that close, the fill at the next open), or on the 2%-of-book stop. The regime filter
  only blocks entries: turning off does not sell anything.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from emporos.research.scans.event_days import reaction_days
from emporos.research.swing.rules import DecisionContext, Intent, LossStop, RegimeFilter

__all__ = ["PostEarningsDrift", "ReactionSessions", "DriftRecord"]

VOLUME_WINDOW = 20
VOLUME_MULTIPLE = Decimal(2)


class ReactionSessions:
    """Session -> the instruments whose results reacted that session, and what was skipped."""

    def __init__(self, by_day: Mapping[date, frozenset[str]], used_by_year: Mapping[int, int],
                 in_session: int, no_session: int) -> None:  # fmt: skip
        self._by_day = dict(by_day)
        self.used_by_year = dict(sorted(used_by_year.items()))
        self.in_session = in_session
        self.no_session = no_session

    @classmethod
    def build(
        cls,
        published: Mapping[str, Sequence[datetime]],
        sessions: Mapping[str, Sequence[date]],
    ) -> ReactionSessions:
        """`published[instrument]` are the events' public times; `sessions[instrument]` are the
        sessions that name traded in the window under test."""
        by_day: dict[date, set[str]] = defaultdict(set)
        used: Counter[int] = Counter()
        in_session = no_session = 0
        for instrument_id, moments in published.items():
            found = reaction_days(moments, sessions.get(instrument_id, ()))
            in_session += found.in_session
            no_session += found.no_session
            for day in found.days:
                by_day[day].add(instrument_id)
                used[day.year] += 1
        return cls({d: frozenset(v) for d, v in by_day.items()}, used, in_session, no_session)

    def reacting_on(self, day: date) -> frozenset[str]:
        return self._by_day.get(day, frozenset())


@dataclass
class DriftRecord:
    qualified_by_year: Counter[int] = field(default_factory=Counter)
    skipped_for_slots: int = 0
    stops: int = 0


class PostEarningsDrift:
    def __init__(
        self,
        reaction_min: Decimal,
        hold_sessions: int,
        max_positions: int,
        reactions: ReactionSessions,
        regime: RegimeFilter,
        stop: LossStop,
    ) -> None:
        if reaction_min <= 0 or hold_sessions < 1 or max_positions < 1:
            raise ValueError("the reaction threshold, the hold and the book size are positive")
        self._reaction_min = reaction_min
        self._hold = hold_sessions
        self._n = max_positions
        self._reactions = reactions
        self._regime = regime
        self._stop = stop
        self.record = DriftRecord()

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        kept = self._kept(context)
        free = self._n - len(kept)
        if free <= 0 or not self._regime.is_on(context):
            return [Intent(n) for n in kept]
        qualified = self._qualified(context)
        self.record.skipped_for_slots += max(0, len(qualified) - free)
        return [Intent(n) for n in (*kept, *(name for _, name in qualified[:free]))]

    def _kept(self, context: DecisionContext) -> list[str]:
        kept: list[str] = []
        for name in sorted(context.holdings):
            holding = context.holdings[name]
            if holding.sessions_held >= self._hold:
                continue
            (last,) = context.view.history(name, 1)
            if self._stop.is_hit(holding, last.close.amount):
                self.record.stops += 1
                continue
            kept.append(name)
        return kept

    def _qualified(self, context: DecisionContext) -> list[tuple[Decimal, str]]:
        found: list[tuple[Decimal, str]] = []
        for name in sorted(self._reactions.reacting_on(context.day)):
            if name not in context.tradable or name in context.holdings:
                continue
            bars = context.view.history(name, VOLUME_WINDOW + 1)
            if len(bars) < VOLUME_WINDOW + 1:
                continue
            reaction = bars[-1].close.amount / bars[-2].close.amount - 1
            earlier = [bar.volume for bar in bars[:-1]]
            mean = Decimal(sum(earlier)) / VOLUME_WINDOW
            if (
                reaction >= self._reaction_min
                and Decimal(bars[-1].volume) >= VOLUME_MULTIPLE * mean
            ):
                found.append((reaction, name))
                self.record.qualified_by_year[context.day.year] += 1
        found.sort(key=lambda pair: (-pair[0], pair[1]))
        return found
