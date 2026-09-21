"""A strategy's recorded verdict: what a curation concluded, and whether it still applies.

A verdict is a fact about ONE configuration: the one that was judged, identified by its behaviour
hash (the resolved config with `enabled` left out, because switching a strategy on does not change
what it does). If the config is later edited, the verdict no longer describes what would run, and
its standing becomes STALE: not "rejected", not "validated", simply not evidence about this config.

Pure data and rules; recording and reading live in persistence, and the decisions that depend on a
verdict (may this strategy start?) live in `session.launch_gate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from emporos.domain.experiments import Verdict


class Standing(StrEnum):
    """Where a strategy stands now: its recorded verdict, judged against the current config."""

    VALIDATED = "validated"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"
    STALE = "stale"  # a verdict exists, for a configuration that is no longer this one
    NONE = "none"  # never curated


@dataclass(frozen=True)
class GateFinding:
    name: str
    outcome: str  # "pass", "fail" or "unknown"
    detail: str


@dataclass(frozen=True)
class RecordedVerdict:
    strategy: str
    behaviour_hash: str
    verdict: Verdict
    gates: tuple[GateFinding, ...]
    capital: str  # the benchmark capital it was judged at, a quoted number
    first_day: str  # ISO dates of the history judged
    last_day: str
    experiment: str
    source: str  # "curation" (recorded by the run itself) or "imported" (from its JSON report)
    recorded_at: datetime
    # What differs between what was judged and what would run (e.g. position sizing): shown beside
    # the verdict, never a reason to change it.
    notes: tuple[str, ...] = ()

    def standing_for(self, behaviour_hash: str) -> Standing:
        if self.behaviour_hash != behaviour_hash:
            return Standing.STALE
        return Standing(self.verdict.value)

    @property
    def failing(self) -> tuple[GateFinding, ...]:
        return tuple(g for g in self.gates if g.outcome == "fail")

    @property
    def unresolved(self) -> tuple[GateFinding, ...]:
        return tuple(g for g in self.gates if g.outcome == "unknown")


def standing_of(verdict: RecordedVerdict | None, behaviour_hash: str) -> Standing:
    return Standing.NONE if verdict is None else verdict.standing_for(behaviour_hash)
