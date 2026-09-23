"""Parity reports in Mongo: append and read, nothing else (EM-185).

Like the verdict book it holds a `Repository` rather than being one, so a report cannot be edited
or deleted through it. The unique index on (strategy, behaviour hash, period, kind) makes a repeated
comparison a no-op rather than a second opinion: the first report for a span stands.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.experiments import Verdict
from emporos.domain.parity import ParityKind, ParityReport
from emporos.domain.verdicts import GateFinding
from emporos.persistence.collections import Collection
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import ParityReportRecord
from emporos.persistence.repository import Repository


class MongoParityReportStore:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        collection: str = Collection.PARITY_REPORTS,
    ) -> None:
        self._records = Repository(database, collection, ParityReportRecord)

    async def append(self, report: ParityReport, report_id: str) -> bool:
        """True when written; False when this span was already reported (the first one stands)."""
        try:
            await self._records.insert(self._record(report, report_id))
        except DuplicateRecordError:
            return False
        return True

    async def latest(self, strategy: str, behaviour_hash: str) -> ParityReport | None:
        """`PaperReconciliationEvidence`: the newest cumulative report for exactly this config."""
        found = await self._records.find(
            {
                "strategy": strategy,
                "behaviour_hash": behaviour_hash,
                "kind": ParityKind.CUMULATIVE.value,
            },
            sort=[("recorded_at", DESCENDING)],
            limit=1,
        )
        return self._report(found[0]) if found else None

    async def dailies(self, strategy: str, behaviour_hash: str) -> list[ParityReport]:
        """Every daily report for this config, oldest session first."""
        found = await self._records.find(
            {
                "strategy": strategy,
                "behaviour_hash": behaviour_hash,
                "kind": ParityKind.DAILY.value,
            },
            sort=[("first_session", ASCENDING)],
        )
        return [self._report(r) for r in found]

    async def daily_on(self, session_date: date) -> list[ParityReport]:
        found = await self._records.find(
            {"kind": ParityKind.DAILY.value, "first_session": session_date.isoformat()}
        )
        return [self._report(r) for r in found]

    @staticmethod
    def _record(report: ParityReport, report_id: str) -> ParityReportRecord:
        return ParityReportRecord(
            _id=report_id,
            strategy=report.strategy,
            behaviour_hash=report.behaviour_hash,
            kind=report.kind.value,
            period=report.period,
            first_session=report.first_session.isoformat(),
            last_session=report.last_session.isoformat(),
            verdict=report.verdict.value,
            gates=[
                {"name": g.name, "outcome": g.outcome, "detail": g.detail} for g in report.gates
            ],
            sessions=report.sessions,
            matched_trades=report.matched_trades,
            metrics={k: dict(v) for k, v in report.metrics.items()},
            recorded_at=report.recorded_at,
            payload=dict(report.payload),
        )

    @staticmethod
    def _report(record: ParityReportRecord) -> ParityReport:
        return ParityReport(
            strategy=record.strategy,
            behaviour_hash=record.behaviour_hash,
            kind=ParityKind(record.kind),
            first_session=date.fromisoformat(record.first_session),
            last_session=date.fromisoformat(record.last_session),
            verdict=Verdict(record.verdict),
            gates=tuple(GateFinding(g["name"], g["outcome"], g["detail"]) for g in record.gates),
            sessions=record.sessions,
            matched_trades=record.matched_trades,
            metrics={k: dict(v) for k, v in record.metrics.items()},
            recorded_at=record.recorded_at,
            payload=dict(record.payload),
        )
