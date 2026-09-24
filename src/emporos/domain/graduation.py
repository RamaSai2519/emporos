"""Where a strategy's configuration stands on the road to real money, as immutable facts.

    RESEARCH ──▶ PAPER ──▶ LIVE_CONSERVATIVE ──▶ PRODUCTION          (RETIRED: the end of the road)

The stage is not a flag someone flips. It is the last of an append-only run of `GraduationEvent`s,
each naming its evidence, and it is bound to ONE configuration by its behaviour hash: edit the
config and the stage no longer describes what would run, so it reads as RESEARCH again (the same
rule `RecordedVerdict.standing_for` applies to a verdict). Being retired is different: it belongs to
the strategy, and no edit un-retires it.

This module is pure: it says what a valid transition and a valid acknowledgement ARE. Who may make
one, and on what evidence, is `emporos.graduation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class GraduationStage(StrEnum):
    RESEARCH = "research"
    PAPER = "paper"
    LIVE_CONSERVATIVE = "live_conservative"
    PRODUCTION = "production"
    RETIRED = "retired"

    @property
    def rank(self) -> int:
        """Position on the road; RETIRED is off it."""
        return _RANK[self]


_RANK = {
    GraduationStage.RESEARCH: 0,
    GraduationStage.PAPER: 1,
    GraduationStage.LIVE_CONSERVATIVE: 2,
    GraduationStage.PRODUCTION: 3,
    GraduationStage.RETIRED: -1,
}


class TransitionKind(StrEnum):
    PROMOTE = "promote"  # exactly one stage up
    DEMOTE = "demote"  # to any lower stage, always allowed
    RETIRE = "retire"  # off the road, for good


class EvidenceKind(StrEnum):
    EXPERIMENT = "experiment"
    VERDICT = "verdict"
    RECONCILIATION_REPORT = "reconciliation_report"
    BROKER_VERIFICATION = "broker_verification"
    ACKNOWLEDGEMENT = "acknowledgement"
    JEV_EXPERIMENT = "jev_experiment"


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer to the evidence a transition rests on: what kind, which record, and a note."""

    kind: EvidenceKind
    ref: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise ValueError("evidence must point at something")


@dataclass(frozen=True)
class GraduationEvent:
    strategy: str
    behaviour_hash: str
    seq: int  # 1, 2, 3 ... per strategy; the ledger's unique key together with `strategy`
    from_stage: GraduationStage
    to_stage: GraduationStage
    kind: TransitionKind
    evidence: tuple[EvidenceRef, ...]
    actor: str
    reason: str
    at: datetime

    def __post_init__(self) -> None:
        for name in ("strategy", "behaviour_hash", "actor", "reason"):
            if not getattr(self, name).strip():
                raise ValueError(f"a graduation event needs a {name}")
        if self.seq < 1:
            raise ValueError("a graduation event's sequence number starts at 1")
        if self.at.tzinfo is None or self.at.utcoffset() != UTC.utcoffset(None):
            raise ValueError("a graduation event's time must be timezone-aware UTC")
        self._check_shape()

    def _check_shape(self) -> None:
        origin, target = self.from_stage, self.to_stage
        if self.kind is TransitionKind.PROMOTE:
            if origin is GraduationStage.RETIRED or target is GraduationStage.RETIRED:
                raise ValueError("a retired strategy cannot be promoted")
            if target.rank != origin.rank + 1:
                raise ValueError(f"cannot promote from {origin.value} to {target.value}")
            if not self.evidence:
                raise ValueError("a promotion must cite its evidence")
        elif self.kind is TransitionKind.DEMOTE:
            if origin is GraduationStage.RETIRED or target is GraduationStage.RETIRED:
                raise ValueError("a demotion cannot start or end at retired")
            if target.rank >= origin.rank:
                raise ValueError(f"cannot demote from {origin.value} to {target.value}")
        elif target is not GraduationStage.RETIRED or origin is GraduationStage.RETIRED:
            raise ValueError("a retirement moves a strategy that is not retired to retired")


def effective_stage(last: GraduationEvent | None, behaviour_hash: str) -> GraduationStage:
    """The stage that applies to THIS configuration, given the strategy's latest event.

    No event is RESEARCH. An event for a different configuration is RESEARCH too (stale), except
    a retirement, which is the strategy's and holds for every configuration."""
    if last is None:
        return GraduationStage.RESEARCH
    if last.to_stage is GraduationStage.RETIRED:
        return GraduationStage.RETIRED
    if last.behaviour_hash != behaviour_hash:
        return GraduationStage.RESEARCH
    return last.to_stage


def short_hash(behaviour_hash: str) -> str:
    """The first eight characters of the hash itself, without its `sha256:` label (a label every
    hash shares would leave one hex character of the phrase actually identifying a config)."""
    return behaviour_hash.removeprefix("sha256:")[:8]


def acknowledgement_phrase(strategy: str, behaviour_hash: str) -> str:
    """The exact text an operator must type to accept the first live deployment of a config."""
    return f"{strategy}@{short_hash(behaviour_hash)} LIVE"


@dataclass(frozen=True)
class LiveAcknowledgement:
    """A human's recorded acceptance of taking ONE configuration live at a named risk tier."""

    strategy: str
    behaviour_hash: str
    operator: str
    typed_phrase: str
    risk_tier: str
    at: datetime

    def __post_init__(self) -> None:
        for name in ("strategy", "behaviour_hash", "operator", "risk_tier"):
            if not getattr(self, name).strip():
                raise ValueError(f"an acknowledgement needs a {name}")
        if self.at.tzinfo is None or self.at.utcoffset() != UTC.utcoffset(None):
            raise ValueError("an acknowledgement's time must be timezone-aware UTC")
        if self.typed_phrase != acknowledgement_phrase(self.strategy, self.behaviour_hash):
            raise ValueError("the typed phrase does not match this strategy and configuration")
