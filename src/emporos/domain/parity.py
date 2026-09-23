"""What a backtest-vs-paper comparison concluded, as a plain value (EM-185).

A `ParityReport` is bound to ONE configuration (its behaviour hash) and one span of sessions, and
is append-only: a later comparison is a new report. Graduation (EM-189) reads the newest
cumulative report through `PaperReconciliationEvidence` and never sees how it was computed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import GateFinding


class ParityKind(StrEnum):
    DAILY = "daily"  # one session
    WEEKLY = "weekly"  # the sessions of one ISO week
    CUMULATIVE = "cumulative"  # every session recorded for this configuration so far


@dataclass(frozen=True)
class ParityReport:
    strategy: str
    behaviour_hash: str
    kind: ParityKind
    first_session: date
    last_session: date
    verdict: Verdict
    gates: tuple[GateFinding, ...]
    sessions: int
    matched_trades: int
    # metric name -> {"backtest", "paper", "absolute", "relative"} as exact decimal strings
    metrics: Mapping[str, Mapping[str, str | None]]
    recorded_at: datetime
    # A daily report carries its session's rows so later reports aggregate without re-running
    # the shadow backtest; other kinds leave it empty.
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.first_session > self.last_session:
            raise ValueError("a parity report cannot end before it begins")
        if self.recorded_at.tzinfo is None:
            raise ValueError("a parity report's time must be timezone-aware")

    @property
    def period(self) -> str:
        first, last = self.first_session.isoformat(), self.last_session.isoformat()
        return first if first == last else f"{first}..{last}"

    @property
    def failing(self) -> tuple[GateFinding, ...]:
        return tuple(g for g in self.gates if g.outcome == "fail")


class PaperReconciliationEvidence(Protocol):
    async def latest(self, strategy: str, behaviour_hash: str) -> ParityReport | None:
        """The newest CUMULATIVE report for exactly this configuration, if any."""
        ...
