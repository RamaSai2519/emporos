"""Doubles for graduation's ports: they implement the same protocols the production adapters do."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from emporos.domain.broker_verification import (
    CRITICAL_BROKER_CHECKS,
    BrokerCheck,
    CheckOutcome,
)
from emporos.domain.experiments import Verdict
from emporos.domain.graduation import GraduationEvent, LiveAcknowledgement
from emporos.domain.parity import ParityKind, ParityReport
from emporos.domain.research_experiments import ExperimentOutcomeLabel
from emporos.domain.verdicts import GateFinding, RecordedVerdict
from emporos.graduation.ports import ExperimentEvidenceView
from emporos.persistence.graduation_store import (
    AcknowledgementExistsError,
    GraduationConflictError,
)

NOW = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
STRATEGY = "orb_v1"
HASH = "abcdef0123456789"


class MemoryLedger:
    """The ledger's guarantees: append-only, and a taken sequence number is refused."""

    def __init__(self) -> None:
        self.events: list[GraduationEvent] = []

    async def latest(self, strategy: str) -> GraduationEvent | None:
        mine = [e for e in self.events if e.strategy == strategy]
        return max(mine, key=lambda e: e.seq) if mine else None

    async def history(self, strategy: str) -> list[GraduationEvent]:
        return sorted((e for e in self.events if e.strategy == strategy), key=lambda e: e.seq)

    async def append(self, event: GraduationEvent) -> None:
        if any(e.strategy == event.strategy and e.seq == event.seq for e in self.events):
            raise GraduationConflictError(f"{event.strategy} #{event.seq} is taken")
        self.events.append(event)


class MemoryAcknowledgements:
    def __init__(self, *acknowledgements: LiveAcknowledgement) -> None:
        self._by_key = {(a.strategy, a.behaviour_hash): a for a in acknowledgements}

    async def get(self, strategy: str, behaviour_hash: str) -> LiveAcknowledgement | None:
        return self._by_key.get((strategy, behaviour_hash))

    async def record(self, acknowledgement: LiveAcknowledgement) -> None:
        key = (acknowledgement.strategy, acknowledgement.behaviour_hash)
        if key in self._by_key:
            raise AcknowledgementExistsError(str(key))
        self._by_key[key] = acknowledgement


class FakeVerdictBook:
    def __init__(self, verdict: RecordedVerdict | None = None) -> None:
        self.verdict = verdict

    async def latest(self, strategy: str) -> RecordedVerdict | None:
        return self.verdict


def recorded(verdict: Verdict = Verdict.VALIDATED, behaviour_hash: str = HASH) -> RecordedVerdict:
    return RecordedVerdict(
        STRATEGY, behaviour_hash, verdict, (), "50000", "2025-01-01", "2026-08-28",
        "EXP-20260924-orb-v1-abcd1234", "curation", NOW,
    )  # fmt: skip


def experiment_view(
    experiment_id: str = "EXP-20260924-orb-v1-abcd1234",
    family: str = "strategy",
    outcome: ExperimentOutcomeLabel = ExperimentOutcomeLabel.ACCEPTED,
    hashes: frozenset[str] = frozenset({HASH}),
    holdout: bool = True,
    unsettled: frozenset[str] = frozenset(),
    assumed: tuple[str, ...] | None = (),
    quarantine: str | None = "q-hash",
) -> ExperimentEvidenceView:
    return ExperimentEvidenceView(
        experiment_id, family, outcome, NOW, hashes, holdout, unsettled, assumed, quarantine
    )


class FakeExperiments:
    def __init__(self, *views: ExperimentEvidenceView) -> None:
        self.views = list(views)

    async def get(self, experiment_id: str) -> ExperimentEvidenceView | None:
        return next((v for v in self.views if v.experiment_id == experiment_id), None)

    async def latest_for(self, behaviour_hash: str, family: str) -> ExperimentEvidenceView | None:
        found = [
            v for v in self.views if v.family == family and behaviour_hash in v.behaviour_hashes
        ]
        return max(found, key=lambda v: v.declared_at) if found else None


class FakeQuarantine:
    def __init__(self, value: str = "q-hash") -> None:
        self.value = value

    async def hash(self) -> str:
        return self.value


class FakeUnresolved:
    def __init__(self, count: int = 0) -> None:
        self._count = count

    async def count(self) -> int:
        return self._count


class FakeSwitch:
    def __init__(self, halted: bool = False) -> None:
        self._halted = halted

    async def halted(self) -> bool:
        return self._halted


def parity_report(
    verdict: Verdict = Verdict.VALIDATED,
    sessions: int = 12,
    gates: tuple[GateFinding, ...] = (),
) -> ParityReport:
    return ParityReport(
        STRATEGY, HASH, ParityKind.CUMULATIVE, date(2026, 9, 1), date(2026, 9, 23), verdict, gates,
        sessions, 40, {}, NOW,
    )  # fmt: skip


class FakeParity:
    def __init__(self, report: ParityReport | None = None) -> None:
        self.report = report

    async def latest(self, strategy: str, behaviour_hash: str) -> ParityReport | None:
        return self.report


class FakeBrokerEvidence:
    def __init__(self, checks: list[BrokerCheck] | None = None) -> None:
        self.checks = checks if checks is not None else all_passing()

    async def critical_checks(self) -> list[BrokerCheck]:
        return self.checks


def all_passing(at: datetime = NOW - timedelta(days=1)) -> list[BrokerCheck]:
    return [BrokerCheck(name, CheckOutcome.PASS, at) for name in CRITICAL_BROKER_CHECKS]
